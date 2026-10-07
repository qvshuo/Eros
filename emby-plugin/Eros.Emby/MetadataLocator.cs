using System;
using System.IO;
using System.Linq;

namespace Eros.Emby;

public static class MetadataLocator
{
    public static string? Locate(string root, string? folder)
    {
        if (string.IsNullOrWhiteSpace(root) || string.IsNullOrWhiteSpace(folder) || !Path.IsPathRooted(root)) return null;
        string key = Path.GetFileName(folder!.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
        if (key.Length == 0 || key == "." || key == ".." || key.Any(char.IsControl)) return null;
        root = Path.GetFullPath(root);
        if (!IsRegularDirectory(root)) return null;
        string? target = Directory.EnumerateDirectories(root)
            .FirstOrDefault(path => string.Equals(Path.GetFileName(path), key, StringComparison.Ordinal));
        return target != null && IsRegularDirectory(target) ? target : null;
    }

    private static bool IsRegularDirectory(string path) => Directory.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0;
    public static bool IsRegularFile(string path) => File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0;
}
