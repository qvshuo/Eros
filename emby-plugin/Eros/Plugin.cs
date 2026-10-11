using System;
using System.ComponentModel;
using System.IO;
using Emby.Web.GenericEdit;
using Emby.Web.GenericEdit.Validation;
using MediaBrowser.Common;
using MediaBrowser.Common.Plugins;
using MediaBrowser.Model.Drawing;
using MediaBrowser.Controller.Plugins;
using MediaBrowser.Model.Attributes;

namespace Eros;

public sealed class PluginOptions : EditableOptionsBase
{
    public override string EditorTitle => "Eros";
    public override string EditorDescription => "";

    [DisplayName("信息文件夹")]
    [Description("选择 Emby 可访问的信息文件夹。")]
    [EditFolderPicker]
    public string MetadataDirectory { get; set; } = "/eros-metadata";

    protected override void Validate(ValidationContext context)
    {
        if (string.IsNullOrWhiteSpace(MetadataDirectory) || !Path.IsPathRooted(MetadataDirectory) || !Directory.Exists(MetadataDirectory))
            context.AddValidationError(nameof(MetadataDirectory), "请选择可访问的信息文件夹。");
    }
}

public sealed class Plugin : BasePluginSimpleUI<PluginOptions>, IHasThumbImage
{
    public static Plugin? Instance { get; private set; }
    public Plugin(IApplicationHost applicationHost) : base(applicationHost) => Instance = this;
    public override string Name => "Eros";
    public override string Description => "读取 Eros 的作品信息和图片。";
    public override Guid Id => new Guid("d6a2ef14-910b-4aa3-b2a8-ded970e55dc1");
    public PluginOptions Options => GetOptions();
    public Stream GetThumbImage() => GetType().Assembly.GetManifestResourceStream("Eros.icon.png")!;
    public ImageFormat ThumbImageFormat => ImageFormat.Png;
}
