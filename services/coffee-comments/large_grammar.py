"""Authored, typed vocabulary and sentence structures; no learned weights.

Each vocabulary bank combines five compatible modifiers with twenty nouns or
verb phrases. These 2,000 fragments are lexical variations, not 2,000 jokes.
One hundred sentence families each have four introductions, yielding 400 patterns.
"""

from large_phrases import lines

SLOT_BANKS = {
    "task": (
        "urgent|recurring|optional|strategic|unplanned",
        "status meeting|budget review|planning session|ticket triage|roadmap discussion|forecast review|"
        "risk assessment|"
        "handover meeting|approval process|requirements review|progress report|team briefing|process review|"
        "document review|scope discussion|milestone review|project update|planning exercise|feedback session|audit",
    ),
    "document": (
        "revised|confidential|unread|shared|unfinished",
        "spreadsheet|slide deck|project plan|risk register|budget sheet|status report|meeting agenda|action list|"
        "handover note|requirements document|process diagram|quarterly report|roadmap|forecast|business case|"
        "tracking sheet|change request|meeting summary|policy document|approval form",
    ),
    "meeting": (
        "weekly|mandatory|extended|rescheduled|impromptu",
        "alignment session|steering committee|planning workshop|status call|team update|budget meeting|"
        "project review|feedback round|kickoff meeting|retrospective|strategy session|briefing|review panel|"
        "handover call|roadmap workshop|coordination meeting|progress check|decision meeting|approval meeting|debrief",
    ),
    "role": (
        "acting|interim|self-appointed|unofficial|temporary",
        "head of optimism|director of delays|manager of loose ends|chief of good intentions|keeper of the backlog|"
        "coordinator of pauses|owner of the action items|guardian of the schedule|head of tiny victories|"
        "supervisor of maybes|champion of warm beverages|director of detours|manager of expectations|"
        "chief of contingencies|owner of the roadmap|head of polite reminders|coordinator of momentum|"
        "guardian of the forecast|manager of the next step|director of sensible breaks",
    ),
    "department": (
        "central|regional|internal|special|temporary",
        "optimism department|deadline office|planning committee|break coordination team|forecasting unit|"
        "coffee council|meeting bureau|paperwork division|urgency department|progress office|"
        "strategy committee|approval unit|mug management team|schedule office|morale department|"
        "clarification bureau|contingency office|calendar committee|documentation team|pause management unit",
    ),
    "outcome": (
        "measurable|strategic|theoretical|unexpected|unconfirmed",
        "progress|momentum|clarity|alignment|efficiency|success|focus|productivity|value|traction|certainty|"
        "readiness|improvement|resolution|cooperation|enthusiasm|stability|direction|confidence|understanding",
    ),
    "metric": (
        "quarterly|estimated|projected|reported|unverified",
        "meeting density|email velocity|spreadsheet activity|calendar pressure|slide production|mug utilization|"
        "inbox growth|deadline movement|task migration|break frequency|optimism level|planning overhead|"
        "progress rate|forecast accuracy|approval speed|document volume|urgency index|coffee demand|"
        "action-item growth|reply-all activity",
    ),
    "excuse": (
        "minor|temporary|unexpected|unresolved|administrative",
        "technical issue|calendar conflict|planning dependency|network mystery|approval delay|scope change|"
        "timing issue|resource question|documentation gap|process exception|handover problem|forecast adjustment|"
        "access request|budget question|schedule revision|coordination issue|reporting delay|version mismatch|"
        "meeting overlap|requirements discussion",
    ),
    "deadline": (
        "original|revised|unofficial|optimistic|imaginary",
        "deadline|delivery date|launch date|handover date|review date|completion date|target date|milestone|"
        "cutoff date|approval date|release date|submission date|rollout date|planning horizon|reporting date|"
        "sign-off date|due date|checkpoint|project finish|go-live date",
    ),
    "project": (
        "flagship|pilot|urgent|internal|long-running",
        "migration|reorganization|integration|redesign|rollout|upgrade|initiative|automation project|"
        "process review|dashboard project|documentation effort|reporting project|planning exercise|"
        "workflow update|system overhaul|cleanup project|improvement plan|budget exercise|strategy review|handover",
    ),
    "black_identity": (
        "unfiltered|uncompromising|minimalist|plain|undecorated",
        "pragmatism|resolve|determination|realism|honesty|purpose|discipline|seriousness|focus|persistence|"
        "efficiency|commitment|conviction|austerity|simplicity|fortitude|pessimism|ambition|dignity|restraint",
    ),
    "foam_identity": (
        "premium|executive|decorative|luxury|artisan",
        "packaging|camouflage|presentation|insulation|branding|theatre|styling|padding|optimism|ceremony|"
        "positioning|distraction|reassurance|flourish|embellishment|wrapping|decoration|polish|prestige|pageantry",
    ),
    "espresso_identity": (
        "concentrated|compressed|pocket-sized|high-density|miniature",
        "ambition|urgency|determination|resolve|optimism|confidence|momentum|purpose|panic|impatience|"
        "audacity|enthusiasm|drama|conviction|focus|strategy|persistence|rebellion|efficiency|defiance",
    ),
    "cleaning_object": (
        "ceremonial|mysterious|unauthorized|experimental|invisible",
        "sponge|brush|cloth|rinse cycle|descaling ritual|maintenance checklist|inspection tool|service manual|"
        "cleaning schedule|rubber gasket|water filter|pipe inspection|drip tray audit|hose|wipe|scrubbing protocol|"
        "diagnostic checklist|maintenance sticker|cleaning committee|inspection form",
    ),
    "mystery_actor": (
        "visiting|undercover|part-time|freelance|interdimensional",
        "office ghost|alien auditor|plumbing wizard|time traveller|cosmic intern|machine whisperer|"
        "parallel technician|quantum janitor|alien consultant|spectral analyst|unlicensed oracle|space accountant|"
        "dimension inspector|poltergeist|ghostly manager|robot philosopher|celestial plumber|temporal investigator|"
        "spectral receptionist|portal supervisor",
    ),
    "mystery_event": (
        "unscheduled|unconfirmed|experimental|unexplained|unrequested",
        "portal inspection|gravity test|firmware seance|timeline adjustment|reality audit|alien onboarding|"
        "haunting rehearsal|wormhole calibration|cosmic status update|dimension upgrade|spectral stand-up|"
        "plumbing prophecy|quantum detour|time-loop rehearsal|dimensional handshake|ghost protocol|signal ceremony|"
        "nebula inspection|reality reboot|extraterrestrial dry run",
    ),
    "sensor_guess": (
        "optimistic|provisional|unverified|creative|unofficial",
        "guess|interpretation|diagnosis|classification|hypothesis|prediction|explanation|theory|estimate|verdict|"
        "reading|conclusion|assumption|assessment|translation|identification|description|summary|reconstruction|annotation",
    ),
    "machine_part": (
        "upper|lower|internal|hidden|auxiliary",
        "valve|pump|pipe|filter|panel|sensor|relay|circuit|hose|seal|connector|switch|gasket|reservoir|fitting|"
        "motor|compartment|bracket|coupling|control board",
    ),
    "office_action": (
        "quietly|formally|carefully|enthusiastically|repeatedly",
        "reviewing the roadmap|revising the forecast|updating the spreadsheet|checking the calendar|"
        "discussing the budget|rearranging priorities|counting the action items|moving the deadline|"
        "clarifying the scope|renaming the project|rewriting the agenda|requesting another update|"
        "circulating the minutes|reopening the ticket|organizing the folders|rescheduling the meeting|"
        "reviewing the review|forwarding the thread|polishing the slides|documenting the process",
    ),
    "reward": (
        "brief|modest|symbolic|well-earned|provisional",
        "break|victory|reprieve|celebration|respite|escape|pause|reward|triumph|success|rescue|intermission|"
        "holiday|retreat|timeout|ceasefire|reset|detour|distraction|recovery",
    ),
}

OFFICE = lines("""
Another {task}, now sponsored by {outcome}.
The {document} remains unread, but the {metric} looks excellent.
Coffee has achieved {outcome}; the {project} is still buffering.
A {reward} before the next {meeting}.
The {role} has approved the {reward}.
The {department} has forecast {outcome}. This cup is the supporting evidence.
The {deadline} remains unchanged, despite the {excuse}.
This cup has more substance than the {document} about the {project}.
The {meeting} has been briefly outvoted by the {reward}.
The {role} would like the {document} to look more enthusiastic.
The {task} can wait while we manufacture {outcome}.
An investment in {outcome}, charged to the {department}.
The {metric} is rising. The {deadline} is approaching. Excellent timing.
This cup is the {reward} promised by the {role}.
The {project} now has a beverage and the {excuse}.
The {department} has mistaken {office_action} for progress.
The {meeting} has produced {outcome}, according to its own minutes.
The {document} has been updated to include the {excuse}.
The {deadline} has requested more {outcome} and fewer explanations.
The {role} is currently {office_action}. Coffee feels more useful.
We have paused the {project} to investigate the {reward}.
The {task} has become a search for {outcome}.
The {meeting} needs {outcome}. The mug is doing its part.
The {document} has enough ambition to frighten the {department}.
The {metric} is impressive if we politely ignore the {excuse}.
The {deadline} is now the responsibility of the {role}.
The {project} has reached the stage of {office_action}.
The {department} has declared the {reward} strategically necessary.
This cup has escaped the {meeting} with its {outcome} intact.
The {document} has offered no opinion on the {reward}.
The {task} has been postponed pending additional {outcome}.
The {role} has attached the {document} instead of an answer.
The {metric} has been revised to make room for the {reward}.
The {deadline} would like a word with the {department}.
The {project} has excellent coffee and surprisingly little {outcome}.
The {meeting} is an elaborate way of {office_action}.
The {excuse} has been promoted to the main feature of the {document}.
The {department} is {office_action}. The machine has already finished.
The {role} has declared {outcome} on the strength of one warm cup.
The {task} has a sponsor. The {reward} has unanimous support.
""")

BLACK = lines("""
All the {black_identity}, none of the {foam_identity}.
The {document} could learn something from this {black_identity}.
No milk, just {black_identity} and the {deadline}.
The {role} has approved a full mug of {black_identity}.
The {meeting} now has a representative from {black_identity}.
The {project} has been supplied with {black_identity}. Results remain uncertain.
An investment in {black_identity}, with no budget for {foam_identity}.
The {department} has selected {black_identity} as its entire presentation strategy.
The beans have brought {black_identity} to the {task}.
This cup pairs {black_identity} with a {reward}.
The {metric} is now supported by {black_identity} alone.
The milk has left the {meeting}. Only {black_identity} remains.
""")

CAPPUCCINO = lines("""
The {project} now comes with {foam_identity}.
The {role} has mistaken {foam_identity} for a complete strategy.
The {meeting} needed {outcome}. We supplied foam.
The {document} has been generously coated in {foam_identity}.
The milk has added {foam_identity} to the {task}.
The {department} has approved a generous budget for {foam_identity}.
The {deadline} looks friendlier under a layer of {foam_identity}.
The {metric} has improved, if we count {foam_identity} as progress.
This cup turns the {excuse} into {foam_identity}.
The {reward} has been upgraded with {foam_identity}.
The frother has delivered {foam_identity}; the {project} can take notes.
The {meeting} has acquired an unnecessary but convincing layer of {foam_identity}.
""")

ESPRESSO = lines("""
The {project} has received a tiny shipment of {espresso_identity}.
The {deadline} has been met with {espresso_identity} and very little liquid.
The {role} has selected {espresso_identity} as the recovery plan.
The {meeting} now runs on {espresso_identity}.
The beans have condensed the {document} into {espresso_identity}.
The {department} has requested a small sample of {espresso_identity}.
The {task} has been escalated to {espresso_identity}.
The {metric} is about to encounter {espresso_identity} in a tiny cup.
A {reward} with an unusually high concentration of {espresso_identity}.
The {excuse} has been overruled by {espresso_identity}.
The {meeting} has been shortened to accommodate the {espresso_identity}.
The {document} recommends {espresso_identity}, preferably in a larger budget.
""")

OTHER = lines("""
Could be the {cleaning_object}. Could be the {mystery_actor}.
Possibly the {mystery_event}. The sensor prefers the {sensor_guess}.
The {machine_part} may be hosting the {mystery_actor}.
Could be maintenance. Could be the {mystery_event} near the {machine_part}.
The classifier has offered the {sensor_guess} about the {mystery_event}.
Maybe the {mystery_actor} is inspecting the {cleaning_object}.
The {machine_part} may know more than the {sensor_guess} suggests.
Could be a routine check. Could be the {mystery_actor} with the {document}.
Possibly the {cleaning_object}, according to the {sensor_guess}.
The sensor has filed the {mystery_event} with the {department}.
Could be servicing. Could be the {mystery_actor} testing the {machine_part}.
Maybe the {mystery_event} needs the {document} before we can explain it.
The {sensor_guess} has been referred to the {mystery_actor} for comment.
The {cleaning_object} may be part of the {mystery_event}. Very reassuring.
Could be the {machine_part}. Could be the {excuse} learning to make noise.
Possibly the {mystery_actor}, now reporting to the {department}.
The {mystery_event} has not been covered in the {document}.
Maybe the {cleaning_object} has encountered the {machine_part} in a new way.
The {sensor_guess} has all the confidence of the {document}.
Could be maintenance. Could be the {mystery_event} missing its {deadline}.
The {mystery_actor} may be responsible. The {sensor_guess} is keeping its options open.
Possibly a cleaning routine, possibly the {mystery_event} with the {excuse}.
The {machine_part} might be auditioning for the {mystery_event}.
The {department} has received the {sensor_guess} and requested more question marks.
""")

COFFEE_OPENERS = lines("""
{weekday}, {clock}. {coffee} joins the agenda.
{coffee} at {clock} on {weekday}.
It's {weekday}, {clock}, and {coffee_lower} is the decision.
The {weekday} plan at {clock}: {coffee_lower}.
{coffee}, clocking in at {clock} on {weekday}.
At {clock} on {weekday}, we have {coffee_lower}.
{weekday} has produced {coffee_lower} at {clock}.
{coffee} has arrived. It's {clock} on {weekday}.
Our {clock} appointment on {weekday}: {coffee_lower}.
{coffee} at {clock}. A very {weekday} development.
Today's {clock} update: {coffee_lower}.
The machine's {clock} proposal is {coffee_lower}.
{coffee} is the headline at {clock}.
At {clock}, {coffee_lower} takes the floor.
{coffee} at {clock}. The meeting can begin.
The {clock} decision is in: {coffee_lower}.
{coffee} at {clock}. We have a starting point.
{coffee} makes the {clock} schedule.
The current {clock} priority is {coffee_lower}.
{coffee}, officially on the agenda at {clock}.
""")

OTHER_OPENERS = lines("""
{weekday}, {clock}. The machine offers a mystery.
Unidentified activity at {clock} on {weekday}.
It's {weekday}, {clock}, and the machine has questions.
The {weekday} event at {clock} remains unclassified.
An unexplained development at {clock} on {weekday}.
At {clock} on {weekday}, something happened here.
{weekday} has produced a machine mystery at {clock}.
Unknown activity. It's {clock} on {weekday}.
Our {clock} puzzle on {weekday} has arrived.
Something happened at {clock}. A very curious {weekday}.
Today's {clock} update is difficult to classify.
The machine's {clock} activity needs a better label.
An unexplained event makes the headlines at {clock}.
At {clock}, the machine takes a mysterious detour.
Unrecognized activity at {clock}. The plot continues.
The {clock} event is in. Its identity is not.
Something stirred at {clock}. We have questions.
A machine mystery makes the {clock} schedule.
The current {clock} activity remains unexplained.
An unidentified event, officially logged at {clock}.
""")
