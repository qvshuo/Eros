FROM mcr.microsoft.com/dotnet/sdk:8.0 AS build
WORKDIR /build
COPY emby-plugin/Eros ./emby-plugin/Eros
RUN dotnet publish emby-plugin/Eros -c Release -o /out
FROM scratch AS artifact
COPY --from=build /out/Eros.dll /Eros.dll
