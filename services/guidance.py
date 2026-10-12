STOP = ("Stop for sharp or worsening pain, dizziness, chest pain or unusual breathlessness. "
        "Seek professional medical advice if symptoms persist.")

RUN_GUIDANCE = {
    "Recovery": dict(
        purpose="Promote recovery while adding easy aerobic volume.",
        warm_up="Walk 3-5 min, then ease into the jog.",
        main_set="{km} km very easy. Walk breaks are fine.",
        recovery="Not needed; the whole run is recovery.",
        cool_down="3-5 min walk and light mobility.",
        rpe="2/10", talk_test="Full sentences with no effort.",
        common_errors="Running too fast; chasing pace or distance.",
        reduce_when="Legs heavy, poor sleep or unusual fatigue: shorten or use walk-jog."),
    "Easy": dict(
        purpose="Aerobic development.",
        warm_up="First 10 min very gentle.",
        main_set="{km} km easy and steady.",
        recovery="Not needed.",
        cool_down="5 min walk and light mobility.",
        rpe="2-3/10", talk_test="Comfortable conversation throughout.",
        common_errors="Drifting into steady pace; comparing with faster days.",
        reduce_when="Fatigue, niggles building, or poor sleep: cut 20-30% or switch to recovery pace."),
    "Easy + Strides": dict(
        purpose="Aerobic development with neuromuscular, relaxed speed.",
        warm_up="10 min easy jog.",
        main_set="{km} km easy, then 4-6 x 15-25 s fast and relaxed strides (not sprinting).",
        recovery="Walk or jog 60-90 s between strides.",
        cool_down="5 min easy jog and walk.",
        rpe="2-3/10 (strides 6-7/10)", talk_test="Easy portions fully conversational.",
        common_errors="Sprinting; tensing shoulders; too many strides.",
        reduce_when="Tight or sore legs: drop strides, keep the run easy."),
    "Fartlek": dict(
        purpose="Gentle introduction to varied pace and aerobic strength.",
        warm_up="10-15 min easy.",
        main_set="{km} km total including 6-8 x 1 min comfortably hard (RPE 6-7), by feel.",
        recovery="2 min easy jog between surges.",
        cool_down="10 min easy.",
        rpe="2-3/10 easy, 6-7/10 surges", talk_test="Short phrases only during surges.",
        common_errors="Surges too hard; not recovering fully between them.",
        reduce_when="Reduce number of surges when tired or after poor sleep."),
    "Steady": dict(
        purpose="Aerobic strength.",
        warm_up="10-15 min easy.",
        main_set="{km} km total with a continuous steady/tempo block (about 20-40 min).",
        recovery="Not applicable (continuous).",
        cool_down="10 min easy.",
        rpe="4-5/10", talk_test="Short sentences; can speak but prefer not to.",
        common_errors="Turning steady into threshold; skipping warm-up.",
        reduce_when="Shorten the block by a third if heavy-legged."),
    "Marathon Pace": dict(
        purpose="Race-specific preparation and pacing practice.",
        warm_up="10-15 min easy plus 2-3 short strides.",
        main_set="{km} km total including marathon-pace blocks (e.g. 2-4 x 2-3 km), built up gradually.",
        recovery="2-3 min easy jog between blocks.",
        cool_down="10 min easy.",
        rpe="5-6/10", talk_test="Short sentences; controlled and sustainable.",
        common_errors="Running faster than goal pace; forcing blocks when fatigued.",
        reduce_when="Cut one block or run by feel if fatigued, hot or under-fuelled."),
    "Threshold": dict(
        purpose="Sustainable speed development.",
        warm_up="15 min easy plus 3 strides.",
        main_set="{km} km total including 3-5 x 5-8 min at threshold effort (or 20-30 min continuous).",
        recovery="1-2 min easy jog between repetitions.",
        cool_down="10-15 min easy.",
        rpe="7/10", talk_test="Only a few words at a time; 'comfortably hard'.",
        common_errors="Starting too fast; turning it into a race.",
        reduce_when="Drop one repetition or run steady instead if tired or ill-feeling."),
    "Intervals": dict(
        purpose="Running economy and aerobic power.",
        warm_up="15 min easy plus 3-4 strides.",
        main_set="{km} km total including 5-6 x 3 min at RPE 7-8 (about 5K effort).",
        recovery="2-3 min easy jog between repetitions.",
        cool_down="10-15 min easy.",
        rpe="7-8/10", talk_test="Not able to speak more than a word or two on reps.",
        common_errors="Over-pacing early reps; short recoveries.",
        reduce_when="Remove reps or convert to steady running when fatigued."),
    "Long Run": dict(
        purpose="Endurance, fueling and durability.",
        warm_up="First 15-20 min very easy.",
        main_set="{km} km conversational. Practise race-day fueling every 30-40 min.",
        recovery="Walk breaks as needed.",
        cool_down="Last 5 min walk; refuel within 30-60 min.",
        rpe="3-4/10", talk_test="Conversation is comfortable throughout.",
        common_errors="Starting too fast; under-fueling; hilly overreach.",
        reduce_when="Cut distance 10-20% if heavy-legged, ill or hot. Never make up missed long runs."),
    "Long Run MP": dict(
        purpose="Endurance, fueling, and marathon-pace finishing practice.",
        warm_up="First 15-20 min very easy.",
        main_set="{km} km: easy for the first part, then final ~{mp} km at marathon pace. Practise race-day fueling.",
        recovery="Walk breaks as needed before the MP block.",
        cool_down="5 min easy jog/walk; refuel within 30-60 min.",
        rpe="3-4/10 easy, 5-6/10 MP finish", talk_test="Conversational early; short sentences in the MP block.",
        common_errors="Easy section too fast; MP block too fast.",
        reduce_when="Drop the MP block and run easy if fatigued. Never make up missed long runs."),
    "Shakeout": dict(
        purpose="Stay loose and fresh.",
        warm_up="Walk 3 min.",
        main_set="{km} km very easy, optional 3-4 relaxed strides.",
        recovery="Not needed.", cool_down="Walk 3-5 min.",
        rpe="2/10", talk_test="Fully conversational.",
        common_errors="Doing too much before an event.",
        reduce_when="Shorten or skip if anything feels off."),
    "Race": dict(
        purpose="Goal race: execute your pacing plan.",
        warm_up="10 min walk/jog and 2-3 strides if comfortable.",
        main_set="42.2 km. Start controlled, settle into goal pace, fuel on schedule.",
        recovery="Walk through aid stations as needed.",
        cool_down="Walk and refuel after the finish.",
        rpe="5-6/10 first half, rising late", talk_test="Short sentences early on.",
        common_errors="Going out too fast; skipping fuel or fluids.",
        reduce_when="Adjust pace for heat or illness."),
    "Event": dict(
        purpose="Tune-up or training event.",
        warm_up="10-15 min easy jog and strides.",
        main_set="{km} km event effort per your priority (A/B/C).",
        recovery="Cool down walk.", cool_down="10 min easy jog/walk.",
        rpe="Varies by event priority", talk_test="Race effort for B events; controlled for C events.",
        common_errors="Racing a C event too hard.",
        reduce_when="Treat as training run if unwell or niggly."),
}


def run_guidance(run_type: str, variant: str, km: float) -> dict:
    key = "Long Run MP" if run_type == "Long Run" and "MP" in (variant or "") else run_type
    g = dict(RUN_GUIDANCE.get(key, RUN_GUIDANCE["Easy"]))
    mp = max(3, min(8, round(km * 0.2)))
    g["main_set"] = g["main_set"].format(km=f"{km:g}", mp=mp)
    g["stop_when"] = STOP
    return g


CORE_TEXT = ("Anti-extension, anti-rotation, anti-lateral flexion: plank, side plank, dead bug, "
             "Pallof press, farmer or suitcase carry.")

_UPPER = ["3-5 exercises of your choice", "2-4 working sets each",
          "Leave 1-3 reps in reserve; avoid failure", "Keep soreness low so it does not affect your next run"]
LEGS = {
    "normal": ["2-4 movements of your choice", "2-3 working sets each", "Leave 2-3 reps in reserve",
               "Not a bodybuilding or maximal-strength session"],
    "reduced": ["Friday long run is over 28 km: reduce leg volume", "1-2 movements, 2 sets each",
                "Leave 3+ reps in reserve", "Avoid anything that causes soreness"],
    "minimal": ["Taper: leg strength volume heavily reduced", "1-2 movements, 1-2 sets", "Light loads, 3+ reps in reserve",
                "Finish feeling fresh"],
    "none": ["Race week: no heavy leg training", "Optional: 10 min easy mobility or a walk"],
}


def strength_guidance(kind: str, level: str, core_minutes: int) -> dict:
    if kind == "Push":
        purpose, bullets = "Upper-body strength, posture, general athleticism.", _UPPER
    elif kind == "Pull":
        purpose, bullets = "Upper back, posture, shoulder health.", _UPPER
    else:
        purpose = "Maintain running performance, improve economy, support durability."
        bullets = LEGS.get(level, LEGS["normal"])
    core = ""
    if core_minutes:
        label = "optional " if kind == "Pull" else ""
        core = f"Core ({label}{core_minutes} min): {CORE_TEXT}"
    return {"purpose": purpose, "bullets": bullets, "core": core}