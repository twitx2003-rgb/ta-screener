"""Discussion channels: simulated members (AI agents, labelled as such) who discuss
the scan's most common patterns, each thread opening with a chart "screenshot"
drawn on from the detection's own geometry.

    select.py     which channels exist and which stocks each discusses
    brief.py      the facts an agent may use, keyed so posts can cite them
    chart_svg.py  the chart image and the marker-style drawings on it
    generate.py   the prompt, the checks every post must pass, and storage

Nothing an agent writes reaches the site unless it cites facts from the brief,
every number in it matches one of those facts, it names only listed personas and
drawings, and it contains no buy/sell instruction.
"""
