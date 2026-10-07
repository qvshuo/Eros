FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /build
COPY emby-plugin/Eros.Emby ./emby-plugin/Eros.Emby
RUN dotnet publish emby-plugin/Eros.Emby -c Release -o /out
FROM scratch AS artifact
COPY --from=build /out/Eros.Emby.dll /Eros.Emby.dll
