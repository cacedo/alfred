# Next Train for Alfred 5

Alfred 5 workflow to show the next three departures between two configured stations using `search.ch/timetable/`.

## Setup

After importing the workflow into Alfred, set these workflow variables in `Configure Workflow...`:

- `NEXTTRAIN_FROM`
- `NEXTTRAIN_TO`

Examples:

- `NEXTTRAIN_FROM=Zurich HB`
- `NEXTTRAIN_TO=Bern`

## Usage

- `nexttrain`: show the next 3 departures for the configured route
- `nexttrain /help`: show setup help

Press `Enter` on a result to open the full route on `search.ch`.

## Data source

- Route search API: `https://search.ch/timetable/api/route.json`
- Timetable website: `https://search.ch/timetable/`
