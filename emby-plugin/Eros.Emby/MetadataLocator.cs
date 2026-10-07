using System;
using System.IO;
using System.Linq;
using MediaBrowser.Model.Serialization;

namespace Eros.Emby;

public sealed class ScanEntry
{
    public string Key { get; set; } = "";
    public string Status { get; set; } = "";
}

public sealed class OutputLocation
{
    public string? Directory { get; set; }
    public string? State { get; set; }
}

public sealed class MetadataLocator
{
    private readonly Func<string, ScanEntry[]> readScan;
    private readonly Func<string, OutputLocation>? readLocation;
    public MetadataLocator(IJsonSerializer serializer) : this(path => serializer.DeserializeFromFile<ScanEntry[]>(path), path => serializer.DeserializeFromFile<OutputLocation>(path)) { }
    public MetadataLocator(Func<string, ScanEntry[]> readScan, Func<string, OutputLocation>? readLocation = null)
    {
        this.readScan = readScan;
        this.readLocation = readLocation;
    }

    public string? Locate(string root, string? folder)
    {
        if (string.IsNullOrWhiteSpace(root) || string.IsNullOrWhiteSpace(folder) || !Path.IsPathRooted(root)) return null;
        string key = Path.GetFileName(folder!.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar));
        if (key.Length == 0 || key == "." || key == ".." || key.Any(c => c < 32)) return null;
        root = Path.GetFullPath(root);
        const string stateRoot = "/eros-state";
        string? state = null;
        string locationPath = Path.Combine(stateRoot, "output.json");
        if (File.Exists(locationPath))
        {
            if (!IsRegularFile(locationPath) || readLocation == null) return null;
            var location = readLocation(locationPath);
            string? relative = location?.Directory;
            state = location?.State;
            if (state == null || state.Length != 16 || state.Any(c => !Uri.IsHexDigit(c))) return null;
            if (string.IsNullOrWhiteSpace(relative) || Path.IsPathRooted(relative)) return null;
            string destination = Path.GetFullPath(Path.Combine(root, relative!));
            string prefix = root.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar;
            if (destination != root && !destination.StartsWith(prefix, StringComparison.Ordinal)) return null;
            string current = root;
            foreach (string part in relative!.Split(new[] { Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar }, StringSplitOptions.RemoveEmptyEntries))
            {
                if (part == ".") continue;
                if (part == "..") return null;
                current = Path.Combine(current, part);
                if (!Directory.Exists(current) || (File.GetAttributes(current) & FileAttributes.ReparsePoint) != 0) return null;
            }
            root = destination;
        }
        if (state == null) return null;
        string scanPath = Path.Combine(stateRoot, state, "scan.json");
        if (!IsRegularFile(scanPath) || (File.GetAttributes(Path.GetDirectoryName(scanPath)!) & FileAttributes.ReparsePoint) != 0) return null;
        var matches = readScan(scanPath).Where(entry => string.Equals(entry.Key, key, StringComparison.OrdinalIgnoreCase)).ToArray();
        if (matches.Length != 1 || matches[0].Status == "conflict" || matches[0].Status == "invalid_key" || !string.Equals(matches[0].Key, key, StringComparison.Ordinal)) return null;
        string target = Path.Combine(root, key);
        if (!Directory.Exists(target) || (File.GetAttributes(target) & FileAttributes.ReparsePoint) != 0) return null;
        return target;
    }

    public static bool IsRegularFile(string path) => File.Exists(path) && (File.GetAttributes(path) & FileAttributes.ReparsePoint) == 0;
}
