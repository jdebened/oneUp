import sys

with open(sys.argv[1]) as f:
    startlines = f.readlines()

with open(sys.argv[2]) as g:
    host = g.readlines()

with open(sys.argv[3]) as h:
    endlines = h.readlines()

with open(sys.argv[4], 'w') as k:
    for line in startlines:
        k.write(line)
    k.write('"')
    k.write(host[0])
    k.write('"')
    for line in endlines:
        k.write(line)

