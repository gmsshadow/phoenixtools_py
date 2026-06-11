from phoenixtools_app.db.engine import make_engine, make_session
from phoenixtools_app.services.mining_jobs import compute_mining_jobs

engine = make_engine()
with make_session(engine) as session:
    report = compute_mining_jobs(session)

print(f"hubs={report.hub_count} jobs={len(report.jobs)} rare={len(report.rare_ores)}")
for j in report.jobs[:10]:
    best = f"{j.best_resource.base_name} res#{j.best_resource.resource_id} (+{j.best_resource.next_complex_output}/wk)" if j.best_resource else "RARE"
    print(f"  {j.weeks_remaining:>3}w  {j.base_name:<25} {j.item_name:<22} avail={j.available:<8} burn={j.weekly_burn:<6} -> {best}")
for r in report.rare_ores[:5]:
    print(f"  RARE {r.item_name}: {len(r.candidates)} candidate deposit(s)")
    for c in r.candidates[:3]:
        print(f"        {c.base_name} res#{c.resource_id} yield={c.resource_yield:g} drop={c.resource_drop} size={c.resource_size} next=+{c.next_complex_output}")
