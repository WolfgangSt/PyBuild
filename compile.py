import os
import subprocess
import sys
import re

cmd = []
cmd_dep = []
out = ""
for i in range(1, len(sys.argv)):
  arg = sys.argv[i]
  if arg.startswith('-o'):
    out = arg
    cmd.append(arg)
  else:
    cmd.append(arg)
    cmd_dep.append(arg)
out = out.lstrip('-o')

def Outdated():
  global out, cmd_dep
  if not os.path.exists(out): #if output does not exist it is outdated
    return True

  otime = os.path.getmtime(out)
  cmd_dep += ['-E', '-M', '-MM']
  deps = subprocess.Popen(cmd_dep, stdout=subprocess.PIPE).communicate()[0]
  deps = deps.decode()
  deps = re.compile('(.+?[^\\\])[ \\r\\n]').findall(deps)
  for dep in deps:
    if (dep[-1] in ['\n', '\r', ':']):
      continue
    d = dep.lstrip()
    if os.path.exists(d):
      dtime = os.path.getmtime(d)
      if dtime >= otime:
        return True #outdated!  
  return False
  
  
err_match = re.compile('(.*?):(\d+):(.*?):')

"""
VC expects output to be in the following format
{filename (line# [, column#]) | toolname} : 
  [anytext] {error | warning} code####: Localizable String
   [
      any text
   ]
   
Example:
c:\sources\ndse\qui\qbuild\main.cpp(29) : error C2065: 'error' : undeclared identifier
"""

def ReformatLine(m):
  file = m.group(1)
  line = m.group(2)
  type = m.group(3).strip()

  #if type == 'error':
  return "{0}({1}) : {2} :".format(file, line, type)
  #else:
  #  return m.string[m.start():m.end()]
 
def Reformat(s):
  return err_match.sub(ReformatLine, s)
  
def Compile():
  print("compiling")
  proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
  pin, perr = proc.communicate()
  pin = Reformat(pin.decode())
  perr = Reformat(perr.decode())

  sys.stdout.write(pin)
  sys.stderr.write(perr)
 
  exit(proc.returncode)
  
  
if Outdated():
  Compile()


