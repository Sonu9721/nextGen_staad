"""Undeformed review geometry; no structural results or supports are inferred."""

from engine.parser import parse_std


def read_model_geometry(path):
    m = parse_std(path)
    return {
        "nodes": {n: p.tolist() for n, p in m.nodes.items()},
        "members": {
            i: {
                "start": v.start,
                "end": v.end,
                "beta": v.beta,
                "releases": sorted(v.releases),
            }
            for i, v in m.members.items()
        },
        "supports": {n: sorted(d) for n, d in m.supports.items()},
        "length_unit": "m",
    }
