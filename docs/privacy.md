# Privacy: what leaves Home Assistant

Bellweather needs one location: your home, or a point you pick on the map.
It sends that location to no one just because a service would like to have
it. Every step where accuracy depends on the exact location runs inside
Home Assistant:

- distances to stations are measured there;
- the nearest station is picked there;
- the radar pixel is read there, and so is the satellite map cell.

The services receive only what each step needs. In most cases that is far
less than the exact point, and for some it is nothing at all.

## The setting

**Location shared with weather services** (setup and options):

| Choice | Point services receive | Area services receive |
|---|---|---|
| Exact location | the exact point | a box centred on the exact point |
| Rounded to about 1 / 5 / **10** / 25 km | the centre of the grid cell that holds the home | a box centred on that cell centre, grown by the cell size |

**The default is 10 km.** The grid is fixed: the same home always gives the
same cell, so repeated requests do not narrow it down.

The shared point can lie up to 0.7 times the cell size from the home:

| Cell | Farthest offset |
|---|---|
| 1 km | 0.7 km |
| 5 km | 3.5 km |
| 10 km | 7.1 km |
| 25 km | 17.7 km |

## What each service receives

| Service | What it receives | Effect of rounding on accuracy |
|---|---|---|
| Radar, and the radar nowcast (RainViewer) | The zoom-7 map tile that holds the point; for the nowcast always the same 3 x 3 block of tiles around it, so the choice of tiles says nothing about where in the tile the home lies. Same for every setting. A tile is 2.8° of longitude wide: about 310 km at the equator, 200 km at 50° latitude | **None.** The pixel of the exact location is read inside Home Assistant |
| Radar coverage map (RainViewer) | The same tile | None |
| Official stations (E-SOH) | A box: 40 km search radius plus the rounding. It grows if you raise the distance limits, up to 100 km. The station names come from the same box | **None.** Every station in the box comes back; the nearest one to the exact location is chosen locally |
| Airports (METAR) | A box: 50 km search radius plus the rounding, larger if you raise the limits, up to 100 km | **None**, for the same reason |
| Model values and the forecast (Open-Meteo) | The shared point | **Small to noticeable.** Global models (9–25 km grids) hardly change within 10 km. High-resolution regional models (1–3 km) and places near hills or coasts do: there 1–5 km gives a more local forecast |
| Forecast verification (Open-Meteo Previous Runs) | The shared point, once a day | Same as above |
| Station heights (Open-Meteo elevation API) | The public coordinates of the stations in the box, only when Open-Meteo is on | None. The set of stations says no more than the box already does |
| Satellite snow map (NSIDC, IMS) | Nothing: the whole daily map is downloaded | None |
| Official nowcast (MET Norway) | The shared point, **only** when the shared point lies in the Nordic area (north of 53.5° N, 10° W to 35° E). Elsewhere it is never asked | Its grid is 1 km: rounding blurs it accordingly |
| Airport archive (Iowa Environmental Mesonet) | **Only if you turn it on** (off by default): the code of the nearest suitable airport. Open-Meteo's Previous Runs then also receives that airport's public coordinates | None. It is not used for the observations, only to start the forecast correction early |
| Your weather entities as forecast members | Nothing leaves Home Assistant | – |
| Place search (Open-Meteo geocoding), only while you add or move a location | **Only the text you type** into the optional search field, when it is a place name (no house number, no comma). Nothing is sent if you leave it empty or use the map | – |
| Address search (OpenStreetMap's Nominatim, or the server you set; empty: off), only while you add or move a location | **Only the text you type**, when it has a house number or a comma, or when Open-Meteo did not know the name (then it went to both). At most one request a second; the same text is not asked again for a day. A street address says exactly where a place is, so type one only if you are fine with OpenStreetMap seeing it, or set your own server | – |
| Height of a new location (Open-Meteo elevation API), only while you add or move a location | The shared point, when the pin was placed by hand. Not asked for Home Assistant's own home, whose height Home Assistant knows | – |

So **"Exact location" buys accuracy only for the forecast and the model
values.** Everything measured is just as precise at 25 km as at the exact
point.

## What rounding does not hide

- **Your IP address.** Every request comes from your internet connection.
  Each service sees your public IP address, and an IP address often gives
  away the town. Bellweather sends no cookies, so a service cannot link
  your requests across IP address changes that way.
- **Sparse areas.** If you live far from other stations or airports, even a
  large box can narrow the area down.
- **The only way to send a service nothing** is to turn it off.

## Logs, diagnostics and attributes

- **Logs.** When a service fails, the log line holds the source and the
  error type, for example `metar failed: ClientResponseError (HTTP 503)`.
  It never holds the request URL, because the URL contains the shared point.
- **Diagnostics.** The download for bug reports leaves out the location,
  the stations you ignore, the station names and the exact distances.
  Three distances to known stations would be enough to find the home, so
  each distance is shown only as a band, such as "5-15 km".
- **Entity attributes** show the station and its distance, so you can see
  where a value came from. They stay inside Home Assistant. Keep that in
  mind before you post screenshots of them.

## Several locations

Each location is handled exactly as above, with its own shared point. Their
requests are not merged into one, so no service learns that two places
belong together from a single request. Answers that are the same for
several locations (radar tiles, the radar index, the daily snow map) are
kept for a short time and handed to every location that asks; the request
itself is the one each location would have sent anyway.

## Stored locally

- **The locations** are stored in the integration's config entry, inside
  Home Assistant, like any other integration setting.
- **Last precipitation.** The time precipitation was last seen is kept in
  Home Assistant's state history, so it survives a restart.
- **Forecast history.** An hourly log of the measured temperature, wind and
  wet hours (70 days), plus the recorded forecasts of your member weather
  entities, is kept in `.storage/bellweather.<location id>`, one per location, and used for the
  forecast's verification. It contains no location. Removing a location
  deletes its history; removing the integration deletes all of them.
