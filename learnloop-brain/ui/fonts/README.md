# Fonts – packaged with the app (no internet at runtime)

Approved 25.11.2026: Frank Ruhl Libre (headings), IBM Plex Sans Hebrew (interface), IBM Plex Mono (code).
All three are licensed under the **SIL Open Font License 1.1**: free to use, bundle and redistribute with the
app, including commercially; the license text must travel with the files; the fonts may not be sold on their own.

## Files this folder must contain (exact names – css/fonts.css points to them)
| File | From |
|---|---|
| `FrankRuhlLibre-VariableFont_wght.ttf` | fonts.google.com/specimen/Frank+Ruhl+Libre → "Get font" → Download (inside the zip) |
| `IBMPlexSansHebrew-Regular.ttf` | fonts.google.com/specimen/IBM+Plex+Sans+Hebrew → Download |
| `IBMPlexSansHebrew-Medium.ttf` | same zip |
| `IBMPlexSansHebrew-SemiBold.ttf` | same zip |
| `IBMPlexMono-Regular.ttf` | fonts.google.com/specimen/IBM+Plex+Mono → Download |
| `OFL.txt` | the license file included in each zip (one copy is enough; they are the same license) |

Copy the files here once. The page loads them from this folder (`/fonts/...`), never from the internet.
Until they are here, the interface uses the installed Windows fonts (David / Segoe UI / Consolas)
and the browser console shows a "404" line for each missing file – harmless.
