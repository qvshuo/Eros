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

namespace Eros.Emby;

public sealed class PluginOptions : EditableOptionsBase
{
    public override string EditorTitle => "Eros";
    public override string EditorDescription => "";

    [DisplayName("元数据目录")]
    [Description("选择挂载到 Emby 的目录。")]
    [EditFolderPicker]
    public string MetadataRoot { get; set; } = "/eros-metadata";

    protected override void Validate(ValidationContext context)
    {
        if (string.IsNullOrWhiteSpace(MetadataRoot) || !Path.IsPathRooted(MetadataRoot) || !Directory.Exists(MetadataRoot))
            context.AddValidationError(nameof(MetadataRoot), "请选择有效的元数据目录。");
    }
}

public sealed class Plugin : BasePluginSimpleUI<PluginOptions>, IHasThumbImage
{
    public static Plugin? Instance { get; private set; }
    public Plugin(IApplicationHost applicationHost) : base(applicationHost) => Instance = this;
    public override string Name => "Eros";
    public override string Description => "读取 Eros 的 NFO 和图片。";
    public override Guid Id => new Guid("d6a2ef14-910b-4aa3-b2a8-ded970e55dc1");
    public PluginOptions Options => GetOptions();
    public Stream GetThumbImage() => GetType().Assembly.GetManifestResourceStream("Eros.Emby.icon.png")!;
    public ImageFormat ThumbImageFormat => ImageFormat.Png;
}
