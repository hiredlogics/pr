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
    dict(id=1, form="clear complete account", expect="UNDERSTOOD",
         text="I paid for two hours at the machine when I arrived, but my daughter felt unwell "
              "and we went to the pharmacy next door to get her medicine, which took longer "
              "than expected because of the queue.",
         keep=[["pharmacy", "medicine"], ["queue"], ["pa"]]),
    dict(id=2, form="one short sentence", expect="UNDERSTOOD",
         text="The ticket machine was broken.",
         keep=[["machine"], ["broken", "fault", "not working", "out of order", "malfunction"]]),
    dict(id=3, form="two-word answer", expect="UNDERSTOOD",
         text="Machine broken", keep=[["machine"], ["broken", "fault", "not working", "malfunction"]]),
    dict(id=4, form="two-word answer", expect="UNDERSTOOD",
         text="Paid online", keep=[["paid", "payment"], ["online"]]),
    dict(id=5, form="spelling mistakes", expect="UNDERSTOOD",
         text="i parkd ther for the docters appointmnt and it ran over becuase the doc was "
              "runing late",
         keep=[["doctor", "appointment"], ["late", "overran", "ran over", "delay"]]),
    dict(id=6, form="poor grammar", expect="UNDERSTOOD",
         text="me and kids go shop, car park machine take my money no ticket come out",
         keep=[["machine"], ["ticket"], ["money", "payment", "paid", "charged", "took"]]),
    dict(id=7, form="incomplete sentence", expect="NEEDS_CLARIFICATION",
         text="I went to the machine and then when I",
         answer="I went to the machine, paid, and when I turned round the car park barrier had closed behind me.",
         keep=[]),
    dict(id=8, form="ambiguous pronoun", expect="NEEDS_CLARIFICATION",
         text="I left my mum at the surgery and she said she would sort it. It wasn't sorted.",
         answer="She was going to pay for the parking by phone and it was never paid.",
         keep=[["surgery"]]),
    dict(id=9, form="unclear cause and effect", expect="EITHER",
         text="I got the ticket because I had paid.",
         answer="I paid for the wrong car park zone, so the payment did not cover this one.",
         keep=[]),
    dict(id=10, form="clear negation", expect="UNDERSTOOD",
         text="I did not leave the car park at any point and I never received the reminder text.",
         keep=[["leave", "left", "depart"], ["text", "reminder", "sms"]],
         held=[(["leave", "left", "depart"], "NEGATED"), (["reminder", "text", "sms"], "NEGATED")]),
    dict(id=11, form="uncertainty", expect="UNDERSTOOD",
         text="I think I paid but I'm not sure whether the payment went through, it might have "
              "been declined.",
         keep=[["pay"], ["declin", "went through"]],
         held=[(["declin", "went through", "pay"], "UNCERTAIN")]),
    dict(id=12, form="conflicting statements", expect="NEEDS_CLARIFICATION",
         text="I paid at the machine. I didn't pay because the machine was out of order.",
         answer="I tried to pay but the machine was out of order, so no payment was taken.",
         keep=[["machine"]]),
    dict(id=13, form="unusual circumstance", expect="UNDERSTOOD",
         text="A swan was blocking the exit lane so I waited with the engine off until the "
              "warden moved it along.",
         keep=[["swan"], ["exit"], ["warden"]]),
    dict(id=14, form="unseen wording", expect="UNDERSTOOD",
         text="The bay lines were repainted that morning and the sign was wrapped up in a bin bag.",
         keep=[["repaint", "lines"], ["sign"], ["bin bag", "covered", "wrapped", "obscured"]]),
    dict(id=15, form="long narrative", expect="UNDERSTOOD", text=LONG,
         keep=[["clinic"], ["chest pain", "unwell", "observ"], ["ticket", "paid", "payment"],
               ["ninety", "90", "wait"]]),
    dict(id=16, form="irrelevant detail", expect="UNDERSTOOD",
         text="It was sunny and I had a lovely sandwich and my cat is called Biscuit. The pay "
              "machine took my card but gave me an error and no receipt.",
         keep=[["error"], ["receipt"], ["card"]]),
    dict(id=17, form="facts already on the notice", expect="UNDERSTOOD",
         text="The notice says I stayed two hours forty seven but I paid for three hours.",
         keep=[["three hours", "3 hours"], ["paid", "payment"]]),
    dict(id=18, form="left and came back", expect="UNDERSTOOD",
         text="I popped out to the bank and then came back and parked again.",
         keep=[["bank"], ["came back", "return"], ["park"]]),
    dict(id=19, form="short and ambiguous", expect="NEEDS_CLARIFICATION",
         text="He said it was fine.",
         answer="The car park attendant told me I did not need to pay for the first hour.",
         keep=[]),
    dict(id=20, form="spelling + negation + uncertainty", expect="UNDERSTOOD",
         text="i dident leav the carpark, not shure if the machin took my card tho",
         keep=[["leave", "left", "depart"], ["machine"], ["card"]],
         held=[(["leave", "left", "depart"], "NEGATED"), (["card", "took"], "UNCERTAIN")]),
]
