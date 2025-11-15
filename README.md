# xtreamer

Provide a valid .m3u files with live/movie streams/urls and it will just export it in Xtream Codes format for use from apps like ZenPlayer, etc.

As I had m3u files with many broken/missing urls for channel icons, it downloads icons to local logos folder, and if they're missing or broken, it builds a custom logo with just the channel name so you can identify it easily from the app.

## Requirements & run
```bash
pip3 install -r requeriments.txt
mkdir logos
```

A valid config.json file, just reuse the one provided and replace:
- credentials with a valid set of credentials
- file is the m3u filename sitting in the same folder
- base_url is the external URL as seen from your player (its used to set the logo url for channels as they're stored locally)

```
python3 app.py
```

First execution downloads icons for all your channels (not for movies) so it can take some time depending on the number of channels and if they have an icon set or not.
