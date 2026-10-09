"""Small newc cpio reader/writer for editing the VM ramdisk in memory."""
import stat
def read(data):
    pos=0;out={}
    while pos+110<=len(data):
        while pos<len(data) and data[pos]==0:pos+=1
        if pos+110>len(data):break
        assert data[pos:pos+6] in (b'070701',b'070702'),pos
        f=[int(data[pos+6+i*8:pos+14+i*8],16) for i in range(13)]
        name=data[pos+110:pos+110+f[11]-1].decode();start=(pos+110+f[11]+3)&~3
        blob=data[start:start+f[6]];pos=(start+f[6]+3)&~3
        if name!='TRAILER!!!':out[name]=(f[1],blob)
    return out
def write(entries):
    result=[]
    for i,(name,(mode,data)) in enumerate([*entries.items(),('TRAILER!!!',(stat.S_IFREG,b''))]):
        n=name.encode()+b'\0';fields=[300000+i,mode,0,0,1,0,len(data),0,0,0,0,len(n),0]
        h=b'070701'+b''.join(f'{v:08x}'.encode() for v in fields)+n;h+=b'\0'*(-len(h)%4)
        result.append(h+data+b'\0'*(-len(data)%4))
    return b''.join(result)

