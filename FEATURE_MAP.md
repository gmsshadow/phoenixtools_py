# Feature map (Rails -> Python desktop)

This document inventories existing app behavior from:

- `README.md` feature list
- `config/routes.rb`
- `app/controllers/*_controller.rb`

It is used as the parity checklist for the Python desktop rewrite.

## Global flows

- **Configuration / Setup**
  - Rails route: `configuration/*` (`NexusController`)
  - Purpose: store Nexus credentials + XML access code, kick off initial setup/import.
  - Python desktop:
    - Screen: `ConfigurationScreen`
    - Actions: Save config; Run initial import; Show progress + errors.

- **Refresh / Updates**
  - Rails routes: `GET /fetch_daily`, `GET /fetch_full` (`HomeController`)
  - Purpose: run daily update vs full update job sequences.
  - Python desktop:
    - Screen: `HomeDashboard`
    - Actions: “Daily refresh”, “Full refresh”, optional scheduling later.

## Screens and actions (from routes)

### Home

- **Home / dashboard**
  - Rails: `root` (`HomeController#index`)
  - Python: `HomeDashboard` (status summary + last updated + quick actions)

- **Setup status**
  - Rails: `GET /setting_up`, `GET /setup_status`
  - Python: progress panel + log view during import.

### Trade routes

- **Trade routes index**
  - Rails: `GET /trade_routes` (`TradeRoutesController#index`)
  - Behavior: show best routes (profitability, time filter, barge slots, blacklist filtering).
  - Python:
    - Screen: `TradeRoutesScreen`
    - Core outputs: list of routes with profitability + constraints.

- **Find routes near a start system**
  - Rails: `POST /trade_routes/find` (`TradeRoutesController#find`)
  - Behavior: filter by keys/no-keys, lifeform filter, optional affiliation filter.
  - Python: search controls in `TradeRoutesScreen` (start system + filters).

- **Orders for a route**
  - Rails: `GET /trade_routes/:id/orders` (`TradeRoutesController#orders`)
  - Python:
    - Screen: `TradeRouteOrdersDialog`
    - Output: order text block (copy/export).

- **Assign barge**
  - Rails: `GET /trade_routes/:id/assign_barge`
  - Python: action button on route row (persist assignment state in DB).

### Bases

- **Base list + sort + filtering**
  - Rails: `GET /bases` (`BasesController#index`)
  - Behavior: show starbases/outposts, filter by affiliation, paginate.
  - Python:
    - Screen: `BasesScreen`
    - Filters: outposts/all affiliations; sort by name/location/id.

- **Base details**
  - Rails: `GET /bases/:id` (`BasesController#show`)
  - Python: `BaseDetailScreen` (tabs listed below)

- **Resource production / mining analysis**
  - Rails: `GET /bases/:id/resource_production`, `GET /bases/mining_jobs`
  - Python:
    - `BaseDetailScreen:ResourcesTab`
    - `MiningJobsScreen` (sorted by weeks remaining + rare ores)

- **Mass production**
  - Rails: `GET /bases/:id/mass_production`
  - Python: `BaseDetailScreen:MassProductionTab`

- **Inventory + item groups + trade items report**
  - Rails: `GET /bases/:id/inventory`, `GET /bases/:id/item_groups`, `GET /bases/:id/trade_items_report`
  - Python:
    - `BaseDetailScreen:InventoryTab`
    - `BaseDetailScreen:ItemGroupsTab` (set item group, group-to-base orders)
    - `BaseDetailScreen:TradeItemsReportTab`

- **Middleman / competitive buys**
  - Rails: `GET /bases/:id/middleman`, `GET /bases/:id/competitive_buys`
  - Python:
    - `BaseDetailScreen:MiddlemanTab`
    - `BaseDetailScreen:CompetitiveBuysTab` (with order generation)

- **Outposts + hub assignment**
  - Rails: `GET /bases/:id/outposts`, `POST /bases/:id/set_hub`
  - Python: `BaseDetailScreen:OutpostsTab` + “set hub” action.

- **Fetch turn**
  - Rails: `GET /bases/:id/fetch_turn`
  - Python: `BaseDetailScreen` action “Refresh this base” (or per-base import).

- **Path to base + shipping jobs**
  - Rails: `GET /bases/path_to_base`, `GET /bases/shipping_jobs`
  - Python:
    - `PathFinderScreen` (start base/system -> destination base -> path + orders)
    - `ShippingJobsScreen` (nearest system filter + cargo/life/ores toggles)

### Star systems

- **Star system list**
  - Rails: `GET /star_systems` (`StarSystemsController#index`)
  - Python: `StarSystemsScreen`

- **Star system details**
  - Rails: `GET /star_systems/:id` (`StarSystemsController#show`)
  - Python: `StarSystemDetailScreen`

- **Shortest path**
  - Rails: `GET /star_systems/shortest_path`
  - Python: `PathFinderScreen` capability (system-to-system path).

- **Fetch celestial bodies for a system**
  - Rails: `GET /star_systems/:id/fetch_cbodies`
  - Python: action “Import cbodies for system”.

### Items

- **Items list / manufacturing list**
  - Rails: `GET /items` (`ItemsController#index`)
  - Python: `ItemsScreen` with mode toggle (all vs manufacturing/producable)

- **Item details**
  - Rails: `GET /items/:id` (`ItemsController#show`)
  - Python: `ItemDetailScreen`

- **Fetch item attributes**
  - Rails: `POST /items/:id/fetch`
  - Python: action “Refresh item attributes”.

- **Profitable but no trade route**
  - Rails: `GET /items/profitable_but_no_trade_route`
  - Python: `OpportunitiesScreen` (or tab within Items).

- **Periphery goods / race preferred goods**
  - Rails: `GET /items/periphery_goods`, `GET /items/race_preferred_goods`
  - Python: filters/actions within `ItemsScreen` generating middleman orders.

### Celestial bodies

- **Celestial body details**
  - Rails: `GET /celestial_bodies/:id` (`CelestialBodiesController#show`)
  - Python: `CelestialBodyDetailScreen`

- **Fetch body data**
  - Rails: `GET /celestial_bodies/:id/fetch`
  - Python: action “Refresh body map/data”.

- **GPI orders**
  - Rails: `GET /celestial_bodies/:id/gpi?ships=N`
  - Python: `GpiPlannerDialog` (ships -> generated orders)

- **Search planets/bodies by attributes**
  - Rails: `GET/POST /celestial_bodies/search`
  - Python: `CelestialSearchScreen` (terrain + typed attributes + populated filter).

## Admin / data inspection

- Rails: `RailsAdmin` mounted at `/data`
- Python desktop replacement:
  - “Data Browser” screen providing table views + basic filtering for core entities.

