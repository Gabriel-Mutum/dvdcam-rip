param(
  [string]$Source,      # folder whose contents go in the image root
  [string]$Output,      # .iso path
  [int]$FileSystems,    # 1 = ISO 9660, 2 = Joliet, 4 = UDF
  [string]$Label = "TESTDISC"
)
$ErrorActionPreference = "Stop"
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Runtime.InteropServices.ComTypes;
public static class IsoWriter {
  public static void Write(object comStream, string path, int blockSize, long blocks) {
    IStream s = (IStream)comStream;
    byte[] buf = new byte[blockSize * 256];
    long remaining = blocks * (long)blockSize;
    using (FileStream fs = new FileStream(path, FileMode.CreateNew, FileAccess.Write)) {
      while (remaining > 0) {
        int n = (int)Math.Min(buf.Length, remaining);
        s.Read(buf, n, IntPtr.Zero);
        fs.Write(buf, 0, n);
        remaining -= n;
      }
    }
  }
}
'@
$fsi = New-Object -ComObject IMAPI2FS.MsftFileSystemImage
$fsi.FileSystemsToCreate = $FileSystems
if ($FileSystems -band 4) { $fsi.UDFRevision = 0x102 }
$fsi.FreeMediaBlocks = 0
$fsi.VolumeName = $Label
$fsi.Root.AddTree($Source, $false)
$result = $fsi.CreateResultImage()
[IsoWriter]::Write($result.ImageStream, $Output, $result.BlockSize, $result.TotalBlocks)
Write-Output "wrote $Output ($($result.TotalBlocks) blocks)"
