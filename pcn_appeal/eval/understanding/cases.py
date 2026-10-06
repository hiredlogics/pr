"""Twenty customer accounts, one notice context.

The expectations are about MEANING, written once and scored by code: whether a
clarification was warranted, which specific details must survive, and which
statements must keep their negation or uncertainty. No expectation is a phrase
rule that production code could read - nothing here is imported by the product.

`expect`:
  UNDERSTOOD           enough meaning exists; asking would be over-clarifying
  NEEDS_CLARIFICATION  the words support materially different readings
  EITHER               a reasonable reader could go either way; scored on the
                       quality of whichever answer was given, never counted as
                       an unnecessary or a missed clarification

`keep`     groups of alternatives; every group needs one member somewhere in the
           packet (summary, events, atoms, concepts, uncertainties).
`held`     (alternatives, polarity): an item mentioning one of them must carry
           that polarity (an UNCERTAIN item may also sit in `uncertainties`).
`answer`   what the customer replies if asked; the second turn must resolve.
`order`    groups of alternatives in the order the account states them; the
           packet must keep that order (event order, or a PRECEDES/FOLLOWS edge).
`cause`    (cause alternatives, effect alternatives): a CAUSES edge, or a single
           event that states both, must connect them.
`ambiguity` MATERIAL: the readings differ in what happened, so asking is required
           and READY_FOR_KNOWLEDGE must stay false until answered.
           NON_MATERIAL: the readings do not change what happened; the
           uncertainty must be preserved and asking is pointless.
           NONE: nothing is ambiguous.
`tags`     which measured abilities the case exercises.
"""
from __future__ import annotations

# The one confirmed notice every case is read against.
NOTICE = {
    "operator_name": "Northgate Parking Ltd", "pcn_number": "00112233",
    "vrm": "AB12CDE", "parking_location": "Retail Park, Leeds",
    "parking_event_date": "01/06/2026", "notice_issue_date": "20/06/2026",
    "entry_time": "10:00", "exit_time": "12:47",
    "alleged_breach": "Overstayed the paid time",
}

LONG = (
    "We arrived about ten and I bought a two hour ticket at the machine by the entrance. "
    "My daughter then complained of chest pain so we went to the walk-in clinic next to the "
    "car park, which has its own entrance, and I left the car where it was. The clinic was "
    "very busy and the nurse wanted her observed for a while, so we were not back until "
    "nearly one. She is fine now, thank you for asking. When we came back there was already a "
    "ticket on the windscreen. I took a photo of the clinic waiting room sign saying the "
    "wait was ninety minutes. The weather was awful too and my phone had died.")

CASES = [
    dict(id=1, ambiguity="NONE", tags="specifics order", form="clear complete account", expect="UNDERSTOOD",
         text="I paid for two hours at the machine when I arrived, but my daughter felt unwell "
              "and we went to the pharmacy next door to get her medicine, which took longer "
              "than expected because of the queue.",
         keep=[["pharmacy", "medicine"], ["queue"], ["pa"]]),
    dict(id=2, ambiguity="NONE", tags="specifics short", form="one short sentence", expect="UNDERSTOOD",
         text="The ticket machine was broken.",
         keep=[["machine"], ["broken", "fault", "not working", "out of order", "malfunction"]]),
    dict(id=3, ambiguity="NONE", tags="specifics short grammar", form="two-word answer", expect="UNDERSTOOD",
         text="Machine broken", keep=[["machine"], ["broken", "fault", "not working", "malfunction"]]),
    dict(id=4, ambiguity="NONE", tags="short", form="two-word answer", expect="UNDERSTOOD",
         text="Paid online", keep=[["paid", "payment"], ["online"]]),
    dict(id=5, ambiguity="NONE", tags="grammar specifics cause_effect", form="spelling mistakes", expect="UNDERSTOOD",
         text="i parkd ther for the docters appointmnt and it ran over becuase the doc was "
              "runing late",
         keep=[["doctor", "appointment"], ["late", "overran", "ran over", "delay"]]),
    dict(id=6, ambiguity="NONE", tags="grammar specifics", form="poor grammar", expect="UNDERSTOOD",
         text="me and kids go shop, car park machine take my money no ticket come out",
         keep=[["machine"], ["ticket"], ["money", "payment", "paid", "charged", "took"]]),
    dict(id=7, ambiguity="MATERIAL", tags="incomplete ambiguous_short", form="incomplete sentence", expect="NEEDS_CLARIFICATION",
         text="I went to the machine and then when I",
         answer="I went to the machine, paid, and when I turned round the car park barrier had closed behind me.",
         keep=[]),
    dict(id=8, ambiguity="MATERIAL", tags="ambiguous_pronoun", form="ambiguous pronoun", expect="NEEDS_CLARIFICATION",
         text="I left my mum at the surgery and she said she would sort it. It wasn't sorted.",
         answer="She was going to pay for the parking by phone and it was never paid.",
         keep=[["surgery"]]),
    dict(id=9, ambiguity="EITHER", tags="cause_effect", form="unclear cause and effect", expect="EITHER",
         text="I got the ticket because I had paid.",
         answer="I paid for the wrong car park zone, so the payment did not cover this one.",
         keep=[]),
    dict(id=10, ambiguity="NONE", tags="negation specifics", form="clear negation", expect="UNDERSTOOD",
         text="I did not leave the car park at any point and I never received the reminder text.",
         keep=[["leave", "left", "depart"], ["text", "reminder", "sms"]],
         held=[(["leave", "left", "depart"], "NEGATED"), (["reminder", "text", "sms"], "NEGATED")]),
    dict(id=11, ambiguity="NONE", tags="uncertainty", form="uncertainty", expect="UNDERSTOOD",
         text="I think I paid but I'm not sure whether the payment went through, it might have "
              "been declined.",
         keep=[["pay"], ["declin", "went through"]],
         held=[(["declin", "went through", "pay"], "UNCERTAIN")]),
    dict(id=12, ambiguity="MATERIAL", tags="conflict", form="conflicting statements", expect="NEEDS_CLARIFICATION",
         text="I paid at the machine. I didn't pay because the machine was out of order.",
         answer="I tried to pay but the machine was out of order, so no payment was taken.",
         keep=[["machine"]]),
    dict(id=13, ambiguity="NONE", tags="specifics order", form="unusual circumstance", expect="UNDERSTOOD",
         text="A swan was blocking the exit lane so I waited with the engine off until the "
              "warden moved it along.",
         keep=[["swan"], ["exit"], ["warden"]]),
    dict(id=14, ambiguity="NONE", tags="specifics", form="unseen wording", expect="UNDERSTOOD",
         text="The bay lines were repainted that morning and the sign was wrapped up in a bin bag.",
         keep=[["repaint", "lines"], ["sign"], ["bin bag", "covered", "wrapped", "obscured"]]),
    dict(id=15, ambiguity="NONE", tags="specifics order cause_effect", form="long narrative", expect="UNDERSTOOD", text=LONG,
         keep=[["clinic"], ["chest pain", "unwell", "observ"], ["ticket", "paid", "payment"],
               ["ninety", "90", "wait"]]),
    dict(id=16, ambiguity="NONE", tags="specifics irrelevant", form="irrelevant detail", expect="UNDERSTOOD",
         text="It was sunny and I had a lovely sandwich and my cat is called Biscuit. The pay "
              "machine took my card but gave me an error and no receipt.",
         keep=[["error"], ["receipt"], ["card"]]),
    dict(id=17, ambiguity="NONE", tags="specifics", form="facts already on the notice", expect="UNDERSTOOD",
         text="The notice says I stayed two hours forty seven but I paid for three hours.",
         keep=[["three hours", "3 hours"], ["paid", "payment"]]),
    dict(id=18, ambiguity="NONE", tags="order specifics", form="left and came back", expect="UNDERSTOOD",
         text="I popped out to the bank and then came back and parked again.",
         keep=[["bank"], ["came back", "return"], ["park"]]),
    dict(id=19, ambiguity="MATERIAL", tags="ambiguous_short ambiguous_pronoun", form="short and ambiguous", expect="NEEDS_CLARIFICATION",
         text="He said it was fine.",
         answer="The car park attendant told me I did not need to pay for the first hour.",
         keep=[]),
    dict(id=20, ambiguity="NONE", tags="grammar negation uncertainty", form="spelling + negation + uncertainty", expect="UNDERSTOOD",
         text="i dident leav the carpark, not shure if the machin took my card tho",
         keep=[["leave", "left", "depart"], ["machine"], ["card"]],
         held=[(["leave", "left", "depart"], "NEGATED"), (["card", "took"], "UNCERTAIN")]),
]


# Written before any live result existed and never used to tune the prompt or the
# contract: if one of these is ever edited to fit the model, it stops being unseen
# and the run that used it does not count as a holdout run.
HOLDOUTS = [
    dict(id="H1", ambiguity="NONE", tags="short specifics", form="short but clear",
         expect="UNDERSTOOD", text="Paid by phone, signal dropped before it confirmed.",
         keep=[["phone", "app"], ["signal"], ["confirm"]]),
    dict(id="H2", ambiguity="NONE", tags="grammar specifics cause_effect", form="malformed English",
         expect="UNDERSTOOD",
         text="car stay long becuz hospital visit fathr, ticket was for 1 hour onli",
         keep=[["hospital"], ["father", "dad"], ["1 hour", "one hour", "an hour"]],
         cause=(["hospital"], ["stay", "longer", "overstay", "long"])),
    dict(id="H3", ambiguity="MATERIAL", tags="ambiguous_pronoun", form="ambiguous language",
         expect="NEEDS_CLARIFICATION",
         text="My friend said it was covered so I did not bother with the machine.",
         answer="He has a resident permit and told me his permit covers visitors in the bay.",
         keep=[["machine"]]),
    dict(id="H4", ambiguity="NONE", tags="order specifics", form="clear leave and return",
         expect="UNDERSTOOD",
         text="We parked at ten, drove to the station to drop my husband at twenty past, then "
              "came back and parked in the same bay around ten forty.",
         keep=[["station"], ["drop"], ["came back", "return"]],
         order=[["station", "drop"], ["came back", "return"]]),
    dict(id="H5", ambiguity="NONE", tags="specifics irrelevant", form="reason irrelevant to the allegation",
         expect="UNDERSTOOD",
         text="I was late because my sister's wedding dress fitting overran. The pay machine "
              "accepted my card and gave me a ticket valid until twelve.",
         keep=[["ticket"], ["twelve", "12"]]),
    dict(id="H6", ambiguity="NONE", tags="negation specifics", form="negated activity",
         expect="UNDERSTOOD",
         text="I never went into the shop and I didn't use the machine because I had a permit "
              "on the dashboard.",
         keep=[["permit"], ["dashboard"]],
         held=[(["shop"], "NEGATED"), (["machine"], "NEGATED")]),
    dict(id="H7", ambiguity="NONE", tags="uncertainty", form="uncertain activity",
         expect="UNDERSTOOD",
         text="I might have gone back to the car around eleven to feed the meter, I can't swear to it.",
         keep=[["eleven", "11"], ["meter", "pay"]],
         held=[(["back to the car", "returned", "return", "meter"], "UNCERTAIN")]),
    dict(id="H8", ambiguity="NONE", tags="specifics", form="unusual customer circumstance",
         expect="UNDERSTOOD",
         text="A film crew had cordoned off the entrance and a marshal waved me into a bay and "
              "told me to ignore the signs.",
         keep=[["film"], ["marshal"], ["ignore", "signs"]]),
    dict(id="H9", ambiguity="MATERIAL", tags="conflict", form="conflicting statements",
         expect="NEEDS_CLARIFICATION",
         text="I was there for twenty minutes only. The receipt shows I arrived at ten and left "
              "after one.",
         answer="The twenty minutes was only the time I spent in the shop; the car was there from ten until one.",
         keep=[["receipt"]]),
    dict(id="H10", ambiguity="NONE", tags="order cause_effect", form="unseen narrative structure",
         expect="UNDERSTOOD",
         text="Why was I charged? Because the app crashed, that's why there was no payment. "
              "Before that I'd been queuing for the exit.",
         keep=[["crash"], ["queu"], ["no payment", "payment"]],
         order=[["queu"], ["crash"]],
         cause=(["crash"], ["no payment", "payment"])),
]
