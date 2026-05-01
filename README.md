# Multiverse Patcher

**Venue patch management for ETC Eos with multi-universe DMX routing**

Multiverse Patcher solves a specific problem for lighting designers: ETC’s educational and touring Eos licences are limited to 2 universes (1024 channels), but real venues have fixtures spread across many more universes. This tool maps your 1024 Eos channels to any venue address across any number of universes, then routes the DMX automatically.

-----

## What it does

- Imports your Eos patch CSV directly (channels, addresses, labels, fixture types)
- Maps channels to venue DMX addresses across unlimited universes
- Searches the full QLC+ fixture library (4,938+ personalities) for correct channel counts
- Shows Eos and venue universe maps with live address visualisation
- Saves patch files in `.mvp` format
- Works with the companion Python DMX Router for automatic sACN re-addressing

## What it does not do

- It does not program lighting cues – Eos handles all programming
- It does not require internet access – runs entirely on your local rig network

-----

## Requirements

|Item                |Details                                   |
|--------------------|------------------------------------------|
|ETC Eos / ETCnomad  |Any licence level                         |
|Mac running macOS   |M1 or later recommended, Python 3 required|
|QLC+ installed      |For fixture library generation (one time) |
|Chrome or Safari    |For the web interface                     |
|sACN-capable network|Standard ETC 2.x.x.x or similar           |

-----

## Installation

### 1. Multiverse Patcher

Copy `venue_patch.html` to `~/Documents/` and start the HTTP server:

```bash
python3 -m http.server 8090 --directory ~/Documents/
```

Open Chrome: `http://127.0.0.1:8090/venue_patch.html`

Or install as a permanent service:

```bash
cp com.multiverse.patcher.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.multiverse.patcher.plist
```

### 2. Fixture Library (one time)

Generate the fixture library from your QLC+ installation. Paste this directly into Terminal:

```bash
python3 << ENDSCRIPT
import os, json, re
QLC_PATH = '/Applications/QLC+.app/Contents/Resources/Fixtures'
OUT_PATH  = os.path.expanduser('~/Documents/fixtures.json')
def parse_qxf(path):
    try:
        with open(path, 'r', encoding='utf-8', errors='ignore') as f:
            data = f.read()
        mfr   = re.search(r'<Manufacturer>([^<]+)</Manufacturer>', data)
        model = re.search(r'<Model>([^<]+)</Model>', data)
        if not mfr or not model:
            return []
        mfr = mfr.group(1).strip()
        model = model.group(1).strip()
        results = []
        for mo in re.finditer(r'<Mode\s+Name="([^"]*)"[^>]*>(.*?)</Mode>', data, re.DOTALL):
            ch = len(re.findall(r'<Channel\s', mo.group(2)))
            if ch > 0:
                results.append({'m':mfr,'n':model,'mode':mo.group(1),'p':ch,'s':(mfr+' '+model+' '+mo.group(1)).lower()})
        return results
    except:
        return []
fixtures = []
for rd, dirs, files in os.walk(QLC_PATH):
    for f in files:
        if f.endswith('.qxf'):
            fixtures.extend(parse_qxf(os.path.join(rd, f)))
json.dump(fixtures, open(OUT_PATH,'w'), separators=(',',':'))
print('Done:', len(fixtures), 'personalities')
ENDSCRIPT
```

### 3. DMX Router

```bash
cp com.eos.dmxrouter.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.eos.dmxrouter.plist
```

Router UI: `http://127.0.0.1:8091`

-----

## Venue Workflow

1. **Export from Eos**: File -> Export -> CSV -> Patch only
1. **Import**: PATCH -> Import Eos CSV in Multiverse Patcher
1. **Enter venue addresses**: Type `U/Addr` format (e.g. `5/25`) in Venue Addr column
1. **Select fixtures**: Use the search field to find QLC+ personalities – sets channel count automatically
1. **Save**: PATCH -> Save As -> save as `current_patch.mvp` in Documents
1. **Done**: DMX Router reloads automatically within 2 seconds

-----

## Signal Chain

```
APC mini mk2 (USB)
    | MIDI
APC Bridge (Python, port 9002)
    | OSC
ETCnomad (Windows, 2.0.0.1)
    | sACN universes 1 & 2
Python DMX Router (Mac, 2.0.0.2, port 8091)
    | sACN venue universes (3, 5, 6, 10, ...)
Venue DMX nodes -> Fixtures
```

-----

## File Format (.mvp)

Patch files are plain JSON:

```json
{
  "CH": {
    "1": {
      "label": "SL spot",
      "eosAddr": "1/107",
      "fixMfr": "ETC",
      "fixName": "ColorSource PAR",
      "fixMode": "8ch",
      "fixParams": 6
    }
  },
  "VP": {
    "1": {"venueAddr": "5/25"}
  },
  "_v": 1,
  "_tool": "Multiverse Patcher"
}
```

-----

## Troubleshooting

|Problem                  |Solution                                                     |
|-------------------------|-------------------------------------------------------------|
|Router shows 0 mappings  |Save patch as `current_patch.mvp` in `~/Documents/`          |
|No fixture search results|Run the fixture library generator script                     |
|Input fps = 0            |Check Eos sACN output is enabled on correct network interface|
|Output fps = 0           |Check venue universe numbers match your patch                |
|Labels not importing     |Eos CSV must include LABEL column – re-export from Eos       |
|Wrong IP at venue        |Update Local IP in Router UI, click Apply + Restart          |

-----

## Repository Contents

|File                          |Description                             |
|------------------------------|----------------------------------------|
|`venue_patch.html`            |Multiverse Patcher web app (single file)|
|`dmx_router.py`               |Python DMX Router                       |
|`com.multiverse.patcher.plist`|Launch agent for HTTP file server       |
|`com.eos.dmxrouter.plist`     |Launch agent for DMX Router             |
|`com.eos.timesync.plist`      |Launch agent for Windows time sync      |
|`gettime.py`                  |Windows time sync server                |

-----

## License

MIT License – free to use, modify and distribute.

-----

*Built for use with ETC ETCnomad educational licence and Akai APC mini mk2*
