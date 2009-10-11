# Todo: add multithreading for faster compilation / spawning

import re
import os.path
import subprocess
import sys
import copy
import string

try:
  import threading as _threading
except ImportError:
  import dummy_threading as _threading
import xml.dom.minidom as dom

###############################################################################
# DOM helper

def getDirectElementsByTagName(node, name):
  return [e for e in node.childNodes 
    if (e.nodeType == e.ELEMENT_NODE) and (e.tagName == name)]

def getFirstElementByTagName(node, name):
  for e in node.childNodes:
    if (e.nodeType == e.ELEMENT_NODE) and (e.tagName == name):
      return e
  return None


###############################################################################
# Environment support

# Per file matched variables
# InputDir      - Directory of the current file
# InputExt      - Extensions of the current file (last .xyz)
# InputName     - Filename without InputExt
# InputFileName - Full Filename without path
# InputPath     - Full Path+Filename


def BatchList():
  global b_BatchList
  global s_BatchFile
  b_BatchList = True
  if not os.path.exists(IntermediateDirectory):
    os.mkdir(IntermediateDirectory)
  s_BatchFile = IntermediateDirectory + "ndselist.rsp"
  return s_BatchFile
  
def BatchListFull():
  global b_FullRebuild 
  b_FullRebuild = True
  return BatchList()

def ResolveMacro(m):
  s = m.group(1)
  try:
    if s == 'BatchList': return BatchList()
    if s == 'BatchListFull': return BatchListFull()
    return eval(s)
  except:
    return os.environ[s]

resolverRe = re.compile('\$\((.*?)\)')
def ResolveMacros(x):
  return resolverRe.sub(ResolveMacro, x)


def AbsrelPath(path, relative):
  return os.path.abspath( 
    os.path.normpath(os.path.join(relative, path)) )
  # return os.path.abspath(file)

def RelPath(path, relative):
  return os.path.relpath(path, relative)

def SetInput(file, relative_path):
  global InputPath, InputDir, InputFileName
  global InputName, InputExt
  
  InputPath = AbsrelPath( file, relative_path )
  InputDir, InputFileName = os.path.split(InputPath)  
  InputDir += os.sep
  InputName, InputExt = os.path.splitext(InputFileName)
    
    
def PrintVars(): # Debug outputs variables
  print("InputDir             :", InputDir)
  print("InputExt             :", InputExt)
  print("InputName            :", InputName)
  print("InputFileName        :", InputFileName)
  print("InputPath            :", InputPath)
  print("ProjectDir           :", ProjectDir)
  print("ProjectExt           :", ProjectExt)
  print("ProjectName          :", ProjectName)
  print("ProjectFileName      :", ProjectFileName)
  print("ProjectPath          :", ProjectPath)
  print("OutputDirectory      :", OutputDirectory)
  print("IntermediateDirectory:", IntermediateDirectory)
  print("ConfigurationName    :", ConfigurationName)
  print("PlatformName         :", PlatformName)


###############################################################################
# Files support

Files = []
def AddFile(file):
  global Files
  file = os.path.normcase(os.path.realpath(file))
  if file in Files: return
  Files.append(file)

###############################################################################
# Argument helpers  
  
def SplitArgs(s, seperators=" ;"):
  b_Terminated = False
  i_Start = 0
  args = []
  for i in range(len(s)):
    c = s[i]
    if c == '"':
      b_Terminated = not b_Terminated
    elif (c in seperators) and (not b_Terminated):
      arg = s[i_Start:i]
      if len(arg) > 0: args.append(arg)
      i_Start = i+1
  arg = s[i_Start:len(s)]
  if len(arg) > 0: args.append(arg)
  return args

def TermChars(s, terms):
  # could optimize this!
  o = ""
  for c in s:
    if c in terms:
      o += '\\'
    o += c
  return o
  
###############################################################################
# Property handling for rules

# shared properties
class ToolProperty:
  def __init__(self, node):
    self.s_Name = node.getAttribute("Name")
  
class StringProperty(ToolProperty):
  def __init__(self, node):
    super().__init__(node)
    self.s_Delimited  = node.getAttribute("Delimited")  or 'false'
    self.s_Delimiters = node.getAttribute("Delimiters") or ";,"
    self.s_Switch     = node.getAttribute("Switch")     or ""
    self.s_DefaultValue = node.getAttribute("DefaultValue")     or ""
  
  def Apply(self, setting):
    setting = setting or self.s_DefaultValue
    if not setting: return ""
    switch = " " + self.s_Switch
    if (self.s_Delimited == 'true'):
      args = SplitArgs(setting, self.s_Delimiters )
      targs = []
      res = ""
      for arg in args:
        res += switch.replace('[value]', arg)      
    else: res = switch.replace('[value]', setting)
    return res

class EnumProperty(ToolProperty):
  def __init__(self, node):
    super().__init__(node)
    self.h_Values = {}
    self.s_DefaultValue = node.getAttribute("DefaultValue") or "0"
    values = getFirstElementByTagName(node, "Values")
    for val in getDirectElementsByTagName(values, "EnumValue"):
      switch = val.getAttribute("Switch")  or ''
      value  = val.getAttribute("Value")   or '0'
      self.h_Values[value] = switch
    
  def Apply(self, setting):
    v = setting or self.s_DefaultValue
    s = self.h_Values[v]
    if len(s) == 0: return ""
    return " " + s
 
 
class BooleanProperty(ToolProperty):
  def __init__(self, node):
    super().__init__(node)
    self.s_DefaultValue = node.getAttribute("DefaultValue") or "false"
    self.s_Switch       = node.getAttribute("Switch")       or ""
    
  def Apply(self, setting):
    v = setting or 'false'
    if v == 'true':
      return " " + self.s_Switch
    return ""


###############################################################################
# Rulefile support

Rules = {}
r_FileSplit = re.compile('"(.*?)"|(.*?)'+os.pathsep)
#r_FileSplit = re.compile('"(.*?)"')
r_argResolverRe = re.compile('\[(.*?)\]')

class Rule:

  def __init__(self, node):
    # load 
    self.a_Properties = []
    self.s_Name           = node.getAttribute("Name")
    if len(self.s_Name) == 0: raise
    Rules[self.s_Name] = self
    self.s_FileExtensions = node.getAttribute("FileExtensions")
    self.CompileExtensionRegex()
    self.s_CommandLine    = node.getAttribute("CommandLine")
    self.s_Outputs        = node.getAttribute("Outputs")
    self.s_ExecutionDesc  = node.getAttribute("ExecutionDescription")
    self.s_SupportsFileBatching = node.getAttribute("SupportsFileBatching") or 'false'
    self.s_BatchingSeparator    = node.getAttribute("BatchingSeparator")
    props = getDirectElementsByTagName(node, "Properties")
    if props:
      for prop in props[0].childNodes:
        if prop.nodeType != dom.Node.ELEMENT_NODE: continue
        pname = prop.getAttribute("Name")  
        prop = eval(prop.tagName + "(prop)")
        self.a_Properties.append( prop )
        
  
  def CompileExtensionRegex(self):
    # compiles s_FileExtensions as regexp
    x = TermChars(self.s_FileExtensions, '^$+.{}[]()|\\')    
    x = x.replace(
      '?','.').replace('*','.*').split(';')
    self.r_FileExtensions = re.compile('('+'$)|('.join(x)+'$)')
  
  def Match(self, ext):
    return self.r_FileExtensions.match(ext)
  
  
  def RunCmd(self, cmd):
    #print(os.path.abspath(".")+':', cmd)
    try:
      #ret = subprocess.call(cmd)
      proc = subprocess.Popen(cmd, stdout=sys.stdout, stderr=sys.stderr, shell=True )
      proc.communicate()
      ret = proc.returncode
      
      
      if ret != 0:
        print( cmd , file=sys.stderr )
        print("Returned with error", ret)
        exit(ret)
    except:
      print( cmd , file=sys.stderr )
      print( sys.exc_info()[1], file=sys.stderr )
      exit(-1)
      
  def GetOutDir(self):
    SetInput("ndse.tmp", ProjectDir)
    outs = ResolveMacros(self.s_Outputs)
    return os.path.dirname(outs)
      
  def RunCmdInPDir(self, cmd):
    cur = os.getcwdb()
    #odir = self.GetOutDir()
    odir = ProjectDir
    if odir:
      if not os.path.exists(odir):
        os.mkdir(odir)
      os.chdir(odir)
    self.RunCmd(cmd)
    os.chdir(cur)
  
  
  def Clean(self, files, attribs):
    out_arg = ""
    for prop in self.a_Properties:
      if prop.s_Name == "OutputFile":
        out_arg = attribs.get(prop.s_Name) or prop.s_DefaultValue
    for file in files:
      SetInput(file, ProjectDir)
      if out_arg:
        outs = ResolveMacros(out_arg)
      else:
        outs = ResolveMacros(self.s_Outputs)
      for out in r_FileSplit.split(outs):
        if not out: continue
        fname = AbsrelPath(out, ProjectDir)
        AddFile(fname)
        # delete the file
        if os.path.exists(fname):
          os.remove(fname)
  
  
  
  def ResolveArgMacro(self, m):
    s = m.group(1)
    a = self.h_Args.get(s)
    if a != None : return a
    return "[" + s + "]"

  
  def ResolveArgMacros(self, s):
    if s.find('[') == -1: return ResolveMacros(s) # dont spam console   
    s = r_argResolverRe.sub(self.ResolveArgMacro, s)
    
    #if s.find('[') != -1:
    #  print("WARNING:", s, "contains [")
    #  exit(0)

    return ResolveMacros(s)
  
  # redo handling of OutputFile!
  def Execute(self, files, attribs, additionalArgs):
    # iterate over all properties and build their command line tokens
    # collect the full command line in s_allArgs for [AllOptions]
    self.h_Args = {}
    s_allArgs = ""
    for prop in self.a_Properties:
      val = attribs.get(prop.s_Name)
      self.h_Args['$' + prop.s_Name] = val or prop.s_DefaultValue
      pval = prop.Apply(val)
      self.h_Args[prop.s_Name] = pval
      s_allArgs += pval
    self.h_Args['AllOptions'] = s_allArgs
    self.h_Args['AdditionalOptions'] = additionalArgs

    
    # TODO: redo rebuild policy
    # currently rebuild is only checked against s_Output generated files!
    #
    # preprocess files
    # check what files need to be rebuild
    # add expected output to outputs
    a_Rebuild = []
    for file in files:
      SetInput(file, ProjectDir)          # set macros for this file
      ftime = os.path.getmtime(InputPath) # get last access of this file
      outs = self.ResolveArgMacros(self.s_Outputs)
      for out in r_FileSplit.split(outs):
        if not out: continue
        fname = AbsrelPath(out, ProjectDir)
        if not os.path.exists(fname):
          if not (file in a_Rebuild): a_Rebuild.append(file)
        else:
          otime = os.path.getmtime(fname)
          if (ftime >= otime) and not (file in a_Rebuild):
            a_Rebuild.append(file)
        AddFile(fname)
   
    
 
    # RECHECK AND REDO THIS (support response files somehow!)
    # => figure how VC uses them (probably compiler magic ...)
    #    if so use some magic that doesnt clash with VC...

    # batch processing mode
    if self.s_SupportsFileBatching == 'true':      
      # VC passes all files no matter if they are out of date or not
      # unless all files are up to date
      s_Rebuild = ""
      needRebuild = False
      for file in files:
        s_Rebuild += '"' + RelPath(file, ProjectDir) + '" '
        if file in a_Rebuild: needRebuild = True
      if needRebuild:  
        self.h_Args['Inputs'] = s_Rebuild.rstrip()
        cmd = self.ResolveArgMacros(self.s_CommandLine)   
        self.RunCmdInPDir(cmd)
      return

    
    # sequential processing mode
    for file in files:
      if not (file in a_Rebuild):
        continue
      
      SetInput(file, ProjectDir)
      self.h_Args['Inputs'] = RelPath(InputPath, ProjectDir)
      cmd = self.ResolveArgMacros(self.s_CommandLine)      
      
      #cmd = "cmd.exe /C echo " + cmd #+ ">nul"
      print( ResolveMacros(self.s_ExecutionDesc) )
      self.RunCmdInPDir(cmd)
 

def LoadRulefile(file):
  x = dom.parse(file)
  f = getFirstElementByTagName(x, 'VisualStudioToolFile')
  rules = getFirstElementByTagName(f, 'Rules')
  cbrules = getDirectElementsByTagName(rules, 'CustomBuildRule')
  for rule in cbrules:
    Rule(rule)



###############################################################################
# Tool configuration support

class ToolConfig:
  def __init__(self, node, index):
    self.s_Name = node.getAttribute("Name")
    if not self.s_Name in Rules:
      print( "Tool not known:", self.s_Name , file=sys.stderr )
      exit(-1)
    self.r_Rule = Rules[self.s_Name]
    self.h_Attributes = {}
    attr = node.attributes
    self.i_ExecutionBucket = index
    self.s_AdditionalOptions = ""
    for i in range(attr.length):
      a = attr.item(i)
      if (a.name == "Name") : continue
      if (a.name == "AdditionalOptions"):
        self.s_AdditionalOptions = a.nodeValue
        continue
      if (a.name == "ExecutionBucket"):
        self.i_ExecutionBucket = int(a.nodeValue)
        continue
      self.ValidateAttribute(a)
      self.h_Attributes[a.name] = a.nodeValue

    
  def ValidateAttribute(self, node):
    # Debugging function verifies if the selected rule has the given property
    for prop in self.r_Rule.a_Properties:
      if prop.s_Name == node.name: return
    print("Warning: Attribute", node.name, "is not defined in Rule", 
      self.r_Rule.s_Name)
    exit(-1)
    
  def Match(self, files):
    matched = []
    for file in files:
      if self.r_Rule.Match(file): matched.append(file)
    return matched
    
  # Single file processing
  def Process(self, files):
    self.r_Rule.Execute(files, self.h_Attributes, self.s_AdditionalOptions)
    
  def Clean(self, files):
    self.r_Rule.Clean(files, self.h_Attributes)

###############################################################################
# Project file support

# Todo: handle file specific configuration settings

Configurations = {}

class Configuration:
  def __init__(self, node):
    self.s_Name, self.s_Platform = node.getAttribute("Name").split('|')
    self.s_OutputDirectory       = node.getAttribute("OutputDirectory")
    self.s_IntermediateDirectory = node.getAttribute("IntermediateDirectory")
    Configurations[self.s_Name] = self
    
    # load Tools
    self.h_Tools = {}
    self.h_ExecutionBucket = {}
    i = 1
    for tool in getDirectElementsByTagName(node, "Tool"):
      #ExecutionBucket
      t = ToolConfig(tool, i)
      self.h_Tools[t.s_Name] = t
      self.h_ExecutionBucket[t.i_ExecutionBucket] = t
      i += 1
      
  def Prepare(self):
    global ConfigurationName
    global ProjectName
    global IntermediateDirectory
    global IntDir
    global OutputDirectory
    global OutDir
    global PlatformName
    
    ConfigurationName = self.s_Name
    PlatformName = self.s_Platform
    IntermediateDirectory = AbsrelPath(ResolveMacros(self.s_IntermediateDirectory), ProjectDir) + os.sep
    OutputDirectory = AbsrelPath(ResolveMacros(self.s_OutputDirectory), ProjectDir) + os.sep
    OutDir = RelPath(OutputDirectory, ProjectDir)
    IntDir = RelPath(IntermediateDirectory, ProjectDir)
      
  def Build(self):
    self.Prepare()
    print( "------ Build started: Project:", ProjectName +  
      ", Configuration:", self.s_Name, "------") 
    for i in range(1, len(self.h_ExecutionBucket) + 1):
      rule = self.h_ExecutionBucket[i]
      matched = rule.Match(Files)
      rule.Process(matched)    
  
  def Clean(self):
    self.Prepare()
    print( "------ Clean started: Project:", ProjectName +  
      ", Configuration:", self.s_Name, "------")
    for i in range(1, len(self.h_ExecutionBucket) + 1):
      rule = self.h_ExecutionBucket[i]
      matched = rule.Match(Files)
      rule.Clean(matched)
    
def CollectFiles(node):
  for dir in getDirectElementsByTagName(node, "Filter"):
    CollectFiles(dir)
    
  for file in getDirectElementsByTagName(node, "File"):
    filename = file.getAttribute("RelativePath")
    AddFile(AbsrelPath(filename, ProjectDir))
  
def LoadProjectfile(file):
  global InputDir, ProjectDir
  global InputExt, ProjectExt
  global InputFileName, ProjectFileName
  global InputName, ProjectName
  global InputPath, ProjectPath

  SetInput(file, os.curdir)
  ProjectDir = InputDir
  ProjectExt = InputExt
  ProjectName = InputName
  ProjectFileName = InputFileName
  ProjectPath = InputPath 
  
  x = dom.parse(file)
  f = getFirstElementByTagName(x, 'VisualStudioProject')
  
  # Todo: ProjectName probably is the Filename - verify this!
  # ProjectName = f.getAttribute("Name")
  
  # load all rules
  tools = getDirectElementsByTagName(f, "ToolFiles")
  if tools:
    for tool in getDirectElementsByTagName(tools[0], "ToolFile"):
      stool = AbsrelPath( tool.getAttribute("RelativePath"), ProjectDir )
      print("Loading Tool:", stool)
      LoadRulefile( stool )
  
  # load configurations
  configs = getFirstElementByTagName(f, "Configurations")
  for config in getDirectElementsByTagName(configs, "Configuration"):
    Configuration(config)
    
  # collect Files
  files = getFirstElementByTagName(f, "Files")
  if files: CollectFiles(files)

###############################################################################
# Mainapp and testcode

# for things to work properly cwd has to be the project dir!

# Visual Studio Settings
if not 'PATH' in os.environ: os.environ['PATH'] = ""
if not 'INCLUDE' in os.environ: os.environ['INCLUDE'] = ""
if not 'LIB' in os.environ: os.environ['LIB'] = ""

"""
LoadRulefile('../NDSE/XML/Rules/pseudo.xml')
LoadProjectfile('../NDSE/QUI.vcproj') ""
os.environ['PATH'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\bin"
os.environ['INCLUDE'] += os.pathsep + "C:\\Program Files\\Microsoft SDKs\\Windows\\v6.1\\Include"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include\\Qt"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include\\QtCore"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include\\QtGui"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include\\QtOpenGL"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include\\QtXml"
os.environ['INCLUDE'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\include\\QtWebKit"
os.environ['INCLUDE'] += os.pathsep + "C:\\Sources\\NDSE\\Python-3.0.1\\Include"
os.environ['LIB'] += os.pathsep + "C:\\Qt\\2009.01\\qt_vc\\lib"
os.environ['LIB'] += os.pathsep + "C:\\Program Files\\Microsoft SDKs\\Windows\\v6.1\\Lib"
os.environ['LIB'] += os.pathsep + "C:\\Sources\\NDSE\\lib\\"
SolutionDir = 'C:\\Sources\\NDSE\\'
"""
# GCC Setting
#LoadRulefile('../NDSE/XML/vcproj/devkit.rules')
#LoadRulefile('Config/Rules/devkit.rules')
LoadProjectfile('TestProject/hello_world.proj')
os.environ['PATH'] += os.pathsep + "C:\\NitroSDK\\devkitPro\\devkitARM\\bin"
os.environ['DEVKIT_ARM'] = 'C:/NitroSDK/devkitPro/devkitARM'


# get a configuration
cname, conf, = Configurations.popitem()
if len(sys.argv) > 1:
  if sys.argv[1] == 'clean':
    conf.Clean()
    exit(0)


conf.Build()