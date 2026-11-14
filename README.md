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
- An external statistic per meter, `mijnted:meter_<device number>`, with one value per
  day for the last 30 days, each on its own date.

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

Use the `mijnted:meter_…` statistic, not the Season total sensor. The sensor's own
long-term statistics are dated when Home Assistant fetched the value, which is a day or
more after the actual readout. The external statistic is dated on the readout day.

## Options

**Update interval**: hours between updates, 1 to 24 (default 6). mijnTED is updated
about once a day, so updating more often does not give fresher data.

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
