using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading;
using MediaBrowser.Controller.Entities;
using MediaBrowser.Controller.Providers;
using MediaBrowser.Model.Configuration;
using MediaBrowser.Model.Entities;
using MediaBrowser.Model.IO;
using MediaBrowser.Model.Logging;

namespace Eros.Emby;

public sealed class ActorImageProvider : ILocalImageFileProvider, IHasOrder
{
    private readonly IFileSystem fileSystem;
    private readonly ILogger logger;
    public ActorImageProvider(IFileSystem fileSystem, ILogManager logManager)
    {
        this.fileSystem = fileSystem;
        logger = logManager.GetLogger("Eros");
    }
    public string Name => "Eros";
    public int Order => 0;
    public bool Supports(BaseItem item) => item is Person;

    public List<LocalImageInfo> GetImages(BaseItem item, LibraryOptions libraryOptions, IDirectoryService directoryService, CancellationToken cancellationToken)
    {
        var images = new List<LocalImageInfo>();
        cancellationToken.ThrowIfCancellationRequested();
        if (!Supports(item)) return images;
        try
        {
            string? path = Locate(Plugin.Instance?.Options.MetadataRoot ?? "", item.Name);
            if (path != null) images.Add(new LocalImageInfo { FileInfo = fileSystem.GetFileInfo(path), Type = ImageType.Primary });
        }
        catch (Exception exception) when (exception is not OperationCanceledException)
        {
            logger.Warn("Eros portrait skipped for {0}: {1}", item.Name, exception.GetType().Name);
        }
        return images;
    }

    public static string? Locate(string root, string name)
    {
        if (!Path.IsPathRooted(root) || string.IsNullOrWhiteSpace(name) || name == "." || name == ".." ||
            Path.GetFileName(name) != name || name.Any(char.IsControl)) return null;
        string folder = Path.Combine(root, "actors");
        if (!Directory.Exists(root) || !Directory.Exists(folder) ||
            (File.GetAttributes(root) & FileAttributes.ReparsePoint) != 0 ||
            (File.GetAttributes(folder) & FileAttributes.ReparsePoint) != 0) return null;
        var allowed = new[] { ".jpg", ".jpeg", ".png", ".webp", ".gif" };
        return Directory.EnumerateFiles(folder).FirstOrDefault(path =>
            string.Equals(Path.GetFileNameWithoutExtension(path), name, StringComparison.Ordinal) &&
            allowed.Contains(Path.GetExtension(path).ToLowerInvariant()) && MetadataLocator.IsRegularFile(path));
    }
}
