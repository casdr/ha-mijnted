# mijnTED for Home Assistant

A custom integration that reads Techem heat cost allocator values from the
[mijnTED](https://mijnted.nl) portal and imports them into Home Assistant, dated on the
day each value belongs to.

## What you get

- One device per heat cost allocator, named after its room plus device number
  (for example "Living room 90000001"), placed in a suggested area for the room. The
  meters are grouped under one device per apartment.
- Per meter:
  - **Season total**: units counted this heating season up to the last readout
    (`state_class: total_increasing`).
  - **Last readout**: units added on the last day Techem read the meters out. The
    `readout_date` attribute gives that date.
  - **Previous season to date**: the meter's value on today's date one year earlier
    (see below).
  - **Not reporting** (diagnostic): on when the portal lists the meter as
    deactivated, or has no value for it on the last readout date.
- Per apartment:
  - **Data outdated** (diagnostic): on when the newest readout is more than 4 days
    old. The portal is normally a day or two behind, so this means readings stopped
    arriving.
  - **Previous season to date** and **Previous season total** (see below).
  - **Estimated cost this season**, when a price per unit is set (see Options).
- An external statistic per meter, `mijnted:meter_<device number>`, and one for the
  household, `mijnted:total`, with one value per day, each on its own date. A meter's
  statistic is named after its device, so renaming the device in Home Assistant
  renames the statistic at the next update.

## Installation

### HACS

1. In HACS, open the menu, choose **Custom repositories** and add
   `https://github.com/casdr/ha-mijnted` as an **Integration**.
2. Install **mijnTED (Techem)** and restart Home Assistant.
3. Go to **Settings → Devices & services → Add integration**, search for **mijnTED** and
   sign in with the e-mail address and password you use on mijnted.nl.

### Manual

Copy `custom_components/mijnted` into the `custom_components` folder of your Home
Assistant configuration, restart, and add the integration as above.

## Comparing with the previous season

Each apartment gets two sensors, for the household total:

- **Previous season to date**: units the previous season had counted on today's date
  one year earlier (`state_class: total`). It moves on at midnight.
- **Previous season total**: the previous season's final total.

Two statistics hold the household total (all meters added up), one value per day:

- `mijnted:total` ("mijnTED total"): this season, from its first day up to the last
  readout. A day counts only once every meter has a value for it, so a day still
  waiting for one meter never shows too low.
- `mijnted:total_previous_season`: the previous season, moved forward one calendar
  year: on 15 November 2026 it shows the value of 15 November 2025. It covers the
  whole previous season, including the dates still to come, so a graph shows where
  last year ended up. 29 February is counted on 28 February.

Accounts with more than one apartment get the apartment's id appended, for example
`mijnted:total_100001`.

The same comparison exists per meter: a **Previous season to date** sensor on each
meter, and a statistic `mijnted:meter_<device number>_previous_season` next to
`mijnted:meter_<device number>`, shifted one year in the same way. A meter that was
not there last season has no value.

```yaml
type: statistics-graph
title: This season and last season
period: day
chart_type: line
stat_types:
  - state
entities:
  - mijnted:total
  - mijnted:total_previous_season
```

Seasons are found from the data: a season ends where the household total drops back
to about zero (the meters reset in early June, not on a fixed date). Both
seasons are fetched once, in the background at two requests per second, which takes a
few minutes, and kept in Home Assistant's storage, so a restart does not fetch it
again. When a new season starts, the season that just ended becomes the previous
season and is fetched once.

## Showing units per day

Heat cost allocator units are not energy, so the Energy dashboard does not accept them.
Use a **Statistics graph** card on the external statistic instead:

```yaml
type: statistics-graph
title: Heating units per day
chart_type: bar
period: day
stat_types:
  - change
entities:
  - mijnted:meter_90000001
```

Use the `mijnted:meter_…` or `mijnted:total` statistic, not the Season total sensor.
The sensor's own long-term statistics are dated when Home Assistant fetched the value,
which is a day or more after the actual readout. The external statistics are dated on
the readout day.

## Options

- **Update interval**: hours between updates, 1 to 24 (default 6). mijnTED is updated
  about once a day, so updating more often does not give fresher data.
- **Price per unit**: the consumption cost per unit, for the **Estimated cost this
  season** sensor. Leave it at 0 (the default) to leave the sensor out. The price is
  on your annual heating statement (*jaarafrekening*): the consumption costs divided
  by the units. Without a statement, a rough figure is the building's heating
  consumption costs divided by all units in the building, or the price from your
  previous home with the same supplier. The estimate covers only the part of the bill
  that depends on the units. Fixed costs, such as the share by floor area, and your
  advance payments are not in it.

## How it works

- Signing in goes through mijnTED's Azure AD B2C page without a browser. Tokens are
  kept in memory and reused while valid. Refresh tokens last at most 24 hours, after
  which the integration signs in again with the stored credentials.
- The integration finds the apartment, billing unit, meters and rooms through the
  portal's own API, so nothing has to be configured by hand.
- It asks the portal for the date of the last readout and imports only days up to that
  date. Days after it repeat the previous value in the portal and are skipped until
  their real value arrives. Weekend days usually arrive on Monday, each with its own
  value.
- Values before the last readout are final. They are fetched once and kept in memory.
- Each update re-imports the last 30 days. Rows for the same day are overwritten, so
  repeated imports do not add up.
- When a counter drops (a new heating season), the new value counts as that day's
  change instead of a negative one.

## Errors and back-off

- **Wrong password**: Home Assistant asks you to sign in again (reauthentication) and
  makes no further sign-in attempts until you do. That keeps a wrong password from
  locking the account.
- **Unsupported sign-in page** (multi-factor authentication, a captcha, new terms of
  use, or a changed page): a repair issue explains what to do and updates pause until
  you reload the integration.
- **Network or server errors**: the interval doubles after each failure, up to 24 hours,
  and returns to normal after the next successful update.

## Known limitations

- This uses mijnTED's unofficial web API, which can change without notice.
- Accounts with multi-factor authentication are not supported.
- Only occupant accounts with residential units work. Landlord accounts that see only
  billing units are not supported.
- Daily values require the portal's meter value export for your apartment. If it is
  off, a repair issue says so and that apartment is skipped.
- The portal's paths take a year. The integration uses the end year of the current
  billing period, as the portal does when it opens. This was worked out from the portal
  rather than documented.
- On first install the statistic starts at the oldest of the 30 imported days, with a
  change of 0 on that day.
- The portal shows no battery or error state of a meter. **Not reporting** can only
  tell that a meter has no value. A meter whose value simply stops changing looks
  the same as a radiator that is off, so it is not noticed.
- Previous-season values per meter need one more backfill after updating from 0.2.0,
  which runs once in the background.
- Meter and room names come from the portal's room codes. Unknown codes are shown
  as-is.
- Requires Home Assistant 2026.10 or newer.

## Development

```bash
python3.14 -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/ruff check . && .venv/bin/ruff format --check . && .venv/bin/mypy && .venv/bin/pytest
```

The test fixtures in `tests/fixtures` are cut from a portal capture with names,
addresses, ids and device numbers replaced.
