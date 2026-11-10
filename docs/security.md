# Security

Bellweather runs inside Home Assistant, the system that opens your doors and
watches your house. Anything that runs there runs with Home Assistant's own
rights. So I treat every line of Bellweather as code that must not be able
to hurt the house, even when the services it talks to are broken, hijacked
or impersonated.

This page lists what Bellweather can do, what it cannot do, every rule it
follows and why, the settings where safety and quality pull in different
directions, and what risk is left.

## The short version

- **No new way into Home Assistant.** Bellweather opens no port, adds no web
  page or API endpoint, no webhook, no websocket command and no action
  (service). It only creates sensors, binary sensors and one weather entity.
- **It only talks out, only over HTTPS, only to fixed addresses**, and never
  follows a redirect.
- **Everything it receives is treated as hostile:**
  - size caps;
  - decompression caps;
  - number, time and text checks.
- **No code from outside ever runs:**
  - no `eval`, no pickle, no shell, no dynamic imports;
  - no third-party Python packages: `requirements` in the manifest is empty.
- **The location stays out of logs and diagnostics.** Error messages are
  reduced to the error type and HTTP status, because library messages
  contain the request URL.
- **Safest by default.** Where safety costs quality, you choose, and the
  option tells you what each choice costs.

## What Bellweather can do inside Home Assistant

| It does | How | Why it is needed |
|---|---|---|
| Read the location and elevation from Home Assistant's settings | `hass.config` | The default location |
| Read the states of entities you picked (your own sensors, a weather entity, forecast members) | state machine, read only | Your sensors beat every public source |
| Ask your picked weather entities for their hourly forecast | the `weather.get_forecasts` action, which only reads | Optional forecast members |
| Write its own history | Home Assistant's storage helper, one file, `.storage/bellweather.<entry id>` | The forecast's verification needs past measurements |
| Make outgoing HTTPS requests | Home Assistant's shared HTTP session | Weather data |

## What it cannot do, by design

- It does not control any device and calls no action except
  `weather.get_forecasts`.
- It does not listen on the network.
- It does not read or write any file except its own storage file.
- It runs no downloaded code and installs no packages.
- It does not need, store or send any Home Assistant token, password or
  API key. No service it uses needs a key.

Changing its settings goes through Home Assistant's own setup and options
dialogs, which only administrators can open.

## Network rules

| Rule | Where | Why |
|---|---|---|
| HTTPS only; any `http://` URL is refused | `core/safety.check_url`, every request | No reading or changing data on the way |
| Certificates are verified | Home Assistant's shared session (default aiohttp TLS verification) | No impersonated service |
| Redirects are not followed | `allow_redirects=False` | A hijacked service cannot point Home Assistant at another host, for example a device on your network. I checked all 11 endpoints on 2026-11-09: none redirects, so this costs nothing |
| Fixed host list; addresses built from a service's answer are checked | RainViewer's index must name `https://tilecache.rainviewer.com`; tile paths must match `/v2/radar/<hex>` | A tampered index cannot send requests elsewhere |
| 30 s timeout per request, 90 s per source, 30 s for the members' forecast action | `ClientTimeout(total=30)`, `asyncio.timeout` | A slow service, or a slow weather entity, cannot hang the update |
| No cookies | an own HTTP session with a dummy cookie jar | No service can set a cookie and recognise your installation across IP address changes |
| Backoff after failures | 5, 10, 20, 40, then 60 minutes, or longer when the service asks with `Retry-After` (up to 6 hours) | A broken or rate-limited service is not hammered by every installation |
| A shared cache that sends nothing new | `core/cache.py`: the key is the full URL with its parameters; errors and empty answers are not kept; at most 24 MB, oldest first out; parsed answers are copied for every caller | Locations near each other ask for the same radar tiles once. A location only ever receives the answer to exactly the request it would have sent |
| The place search sends only what you type | `core/geocode.py`: trimmed, control characters removed, at most 100 characters, nothing under 2; only fixed parameters besides it; results are checked (coordinates in range, heights from −500 to 9000 m, names cut to 80 characters) | It runs only while you add or move a location, and only if you type into the field |
| The address search follows Nominatim's usage policy | `core/geocode.AddressSearch`: only searches you start, never as you type; one request at a time and at least a second apart; answers cached for a day; the application named in the User-Agent; results shown with the OpenStreetMap attribution; the same checks on results as above. The server is a setting: https only, no user name, password, query or fragment (`address_server`); empty turns it off | The policy (https://operations.osmfoundation.org/policies/nominatim/) limits all users of an application together to one request a second and asks that applications can switch or stop without an update: the setting does that |
| A new location takes `weather.<name>` | `weather.claim_entity_id`: only when the location's weather entity is first created; only if another integration's entity holds that exact id; that entity is renamed to the next free `weather.<integration>_<name>` through Home Assistant's entity registry, and the log says so. Existing entities are never touched | The rename is the documented behaviour (README). Home Assistant moves the renamed entity's history and statistics (`recorder/entity_registry.py`, checked in 2026.10.4) |
| Identifying User-Agent, nothing personal | `bellweather/<version> (Home Assistant custom integration; https://github.com/lauden-io/Bellweather)` | Services ask for it: MET Norway requires the app's name and version and a contact (an e-mail address, a website, or a public repository where issues can be opened), or it may block the app without warning. It names the software and the project's repository, never you |

These are all the addresses Bellweather contacts:

- `api.rainviewer.com`, `www.rainviewer.com`, `tilecache.rainviewer.com`;
- `observations.meteogate.eu`;
- `aviationweather.gov`;
- `api.open-meteo.com`, `previous-runs-api.open-meteo.com`;
- `geocoding-api.open-meteo.com`, only when you type a place search;
- `nominatim.openstreetmap.org` (or the server you set instead), only when
  you type an address;
- `noaadata.apps.nsidc.org`;
- `api.met.no`, only for points in the Nordic area;
- `mesonet.agron.iastate.edu`, only if you turn the airport archive on.

## Everything received is hostile

| Threat | Rule |
|---|---|
| A huge response eats memory | A hard size cap per service, counted while reading and after decompression: E-SOH 4 MB, airports 2 MB, small JSON answers 1 MB, the forecast 2 MB, map tiles 1 MB, the colour table 64 KB, the snow-map index 300 KB, the airport archive 1.5 MB, the snow map 4 MB. Each is many times what the service sends today (measured 2026-11-09), so the caps cost nothing |
| Many items in a small answer | Item caps: E-SOH at most 500 stations, 100 parameters and 500 times each; airports at most 1,000 reports; station list 200; radar 24 frames, at most 13 new per update. Entries outside the requested box are dropped before anything else is done with them |
| A CSV header with thousands of columns | CSV is read column by column; the header may have at most 16 fields and 1 KB |
| A "zip bomb" | PNG tiles must be exactly 256 x 256 pixels, and the decompressed size is limited to what the header promises. Snow map: streamed in 1 MB pieces, at most 45 MB in total and 8 KB per line, and the read stops as soon as the needed rows are found |
| Malformed data crashes the update | Every parser checks types, and each station, report or frame is parsed on its own, so a broken one is skipped and the rest still count. A failing source is isolated: the others still answer. A `Retry-After` header is clamped as a number before it is used |
| Your own inputs | Your sensors and weather entities are checked like any service: finite, within physical limits, converted safely. Bellweather's own entities can never be picked as inputs, so it cannot feed on itself |
| Too many requests caused by an answer | Station heights: at most one batch of 100 per update, and a failed batch waits a day. The official nowcast and every source back off after failures |
| `NaN`, `inf` or absurd numbers steer the choice | `core/safety.finite` everywhere; physical limits per value (for example -90 to 60 °C); coordinates must be valid |
| A time in the future would never age out | Readings more than 10 minutes in the future are ignored |
| Strange text in Home Assistant | Station names: control and format characters (bidi overrides, zero-width) removed, at most 80 characters. Station ids must match `[A-Za-z0-9._:-]{1,40}` |
| Slow regular expressions | Weather codes are cut to 64 characters and 8 groups before any pattern runs; the patterns have no nested repetition |
| Blocking Home Assistant | PNG decoding, the snow map, the radar nowcast, the colour table, the station answer and the daily forecast scoring run in a worker thread, never in Home Assistant's event loop |
| A damaged storage file | Every stored value is checked on load; anything unexpected is dropped. If the file cannot be read at all, Bellweather starts fresh instead of failing |

## Privacy rules

[docs/privacy.md](privacy.md) has the full table of what each service
receives. The rules behind it:

- **Least data:**
  - point services get the rounded point;
  - area services get a box and the nearest station is picked locally;
  - the radar and the snow map get no point at all.
- **The default is rounding to about 10 km**, adjustable from exact to
  25 km.
- **No location in logs:** a failing source is logged as, for example,
  `metar failed: ClientResponseError (HTTP 503)`, never with the URL.
- **No location in diagnostics:**
  - location, coordinates and ignored stations are removed;
  - station names are left out;
  - distances are shown as bands, because three exact distances to known
    stations would give the home away;
  - for values measured by a station, an airport or your own sensor, the
    value and its exact time are left out too: matched against public
    archives they would name the station;
  - the config entry's id is random, not derived from the location.
- **No hint from request patterns:**
  - the radar nowcast always fetches the same 3 x 3 block of tiles around
    the home's tile, so which neighbours are asked for says nothing about
    where in the tile the home lies;
  - the colour table is fetched on a fixed schedule, not when the radar
    shows something at the home;
  - whether the official nowcast is asked is decided from the shared
    point, not the exact one.
- **The source status sensor shows errors without URLs.**
- **Turning a source off sends it nothing.**

## Where safety and quality pull apart: your choice

| Setting | Default (safest) | Choosing the other way buys | …and costs |
|---|---|---|---|
| Location shared with weather services | Rounded to about 10 km | 1–5 km or exact: the forecast and model values answer closer to your home, which matters near hills and coasts | Point services learn your location more precisely |
| Rounded to 25 km (the other direction) | – | Hides more | The forecast answers for a point up to 18 km away; high-resolution models lose their edge |
| Sources | All on | Turning one off: that service learns nothing | Values become less direct or less local; no nowcast without the radar |
| Airport archive | Off | The forecast correction works from day one instead of after about 20 days | One more service (Iowa Environmental Mesonet) learns your nearest airport's code |
| Distance and height limits | Moderate | Larger: a value is found more often | Values from farther away describe your place less well |

Each option's description in Home Assistant states the same trade-off.

## What risk is left

I would rather say it than have you find out:

- **Home Assistant gives custom integrations full rights.** That is how
  Home Assistant works, and no integration can change it. Bellweather keeps
  the code small, uses no third-party packages and checks every input. You
  can read every line before installing it; please do.
- **Your IP address is visible to every service**, as with any internet
  request. It often reveals your town. Rounding cannot hide it; a VPN or
  proxy for Home Assistant can.
- **Trust in the services' data.** A service that lies within plausible
  limits, for example 2 °C too warm, cannot be caught by any check.
  Mitigations:
  - several sources;
  - the option to ignore a station;
  - attributes that show where each value came from;
  - the forecast scorecard.
- **TLS trust.** Bellweather trusts the certificate authorities that Home
  Assistant trusts.
- **Updates.** A future version could be malicious if the repository were
  taken over. My rules for the repository:
  - two-factor authentication;
  - a protected main branch;
  - releases only from reviewed commits.

  Updating is always your decision in HACS.

## A decision: exact distances on the entities

Each value's attributes show the station and its exact distance. With three
of them, someone could work out the home's position. I keep them exact on
purpose:

- they are the core of "you can see why it says what it says";
- they stay inside Home Assistant;
- anyone who can see them there can also see the home location itself
  (`zone.home`), so they reveal nothing new to them.

The risk is screenshots or exports shared outside, and this page and
[privacy.md](privacy.md) warn about it. The diagnostics download, which is
made to be shared, has distance bands only.

## Independent review

Before the first install, a separate reviewer with no part in writing the
code read all of it and tried to break it, with test inputs (2026-11-09).
It found:

- 3 high issues:
  - a CSV header bomb;
  - an E-SOH answer whose parsing cost grew quadratically;
  - the HTTP reader cut honest large answers short, while a compressed
    hostile answer could still reach full size;
- 6 medium issues:
  - an overflowing `Retry-After` header;
  - oversized PNG tiles;
  - too generous caps;
  - a station list that kept growing;
  - an answer that could trigger thousands of elevation requests;
  - no deadline per source;
- 10 low issues, mostly privacy side channels and inputs that were not
  checked.

All of them are fixed, and each has a regression test in
`tests/test_review_fixes.py`.

The same reviewer then checked the fixes. It confirmed them and found a few
smaller points the fixes had introduced:

- the snow map was downloaded for places outside it;
- the radar nowcast fetched tiles twice;
- quoted CSV headers could fail;
- a colour-table failure made the whole radar fail.

It also flagged an unsalted location hash as the entry id. All of these are
fixed too, with tests in `tests/test_rereview_fixes.py`.

## How I work on it

- Nothing from my own home goes into the repository: no addresses,
  coordinates, room or device names, entity ids or measurements. Tests and
  examples use public places (Paris, Oslo, Chicago, airports).
- No secrets in the repository, not even test keys. Bellweather needs none.
- The tests cover the safety rules too: `tests/test_forecast_security.py`
  feeds a decompression bomb, an endless line, a tampered radar index, NaN
  and infinity, future times, path-like station ids, oversized weather
  codes, and error texts containing coordinates.
- I test against real Home Assistant without lowering its protections. For
  example, the SSH add-on stays in protection mode.

## Reporting a vulnerability

See [SECURITY.md](../SECURITY.md).
