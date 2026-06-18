from pathlib import Path
import re
p=Path(r"g:\!olimp\olimp\templates\game.html")
s=p.read_text(encoding='utf-8')
pattern=re.compile(r'{%\s*(\w+)(.*?)%}',re.S)
tokens=[]
for m in pattern.finditer(s):
    tok=m.group(1)
    rest=m.group(2).strip()
    line=s.count('\n',0,m.start())+1
    tokens.append((line,tok,rest,m.start()))
stack=[]
errors=[]
for line,tok,rest,pos in tokens:
    if tok=='if':
        stack.append(('if',line,pos))
    elif tok=='endif':
        if stack and stack[-1][0]=='if':
            stack.pop()
        else:
            errors.append(('unmatched endif',line,pos))
print('IF count:', sum(1 for t in tokens if t[1]=='if'))
print('ENDIF count:', sum(1 for t in tokens if t[1]=='endif'))
print('\nUnclosed IF stack (top last):')
for item in stack[-20:]:
    print('  at line',item[1])
print('\nUnmatched endif occurrences:')
for e in errors:
    print('  at line',e[1])
if stack:
    ln=stack[-1][1]
    lines=s.splitlines()
    start=max(0,ln-6)
    end=min(len(lines),ln+6)
    print('\nContext around last unclosed if (lines {}-{}):'.format(start+1,end))
    for i in range(start,end):
        print(f"{i+1:5d}: {lines[i]}")
print('\n--- tail of file ---')
for i,line in enumerate(s.splitlines()[-80:], start=max(1,len(s.splitlines())-79)):
    print(f"{i:5d}: {line}")
