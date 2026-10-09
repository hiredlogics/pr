"""Notices transcribed from the client's own sample images.

`sample notic/` holds photographs of real parking charge notices. Each entry
below is one of those notices as text, field for field, with nothing added:
where a notice does not state the permitted period, neither does the entry,
because that absence is often the appeal.

Keeper names and addresses are replaced with placeholders - they are real
people's details and nothing in the pipeline needs them to be genuine. Every
other field (operator, site, VRM, dates, times, charge, wording of the
contravention, trade body) is as printed.

These are the SEEN cases. The generated variations in case_matrix.py use the
same shapes with different operators and sites, so a pass here says the
pipeline handles the client's real post, and a pass there says it generalises.
"""

# Each: id, operator, trade body, site, VRM, event date, issue date, charge,
# the contravention exactly as worded, and the text the pipeline receives.

ECP_PARENT_CHILD = dict(
    case_id="R-ECP-PARENTCHILD",
    operator="Euro Car Parks",
    ata="BPA",
    site="Sainsburys - Cromwell Road",
    vrm="RX75VPP",
    event_date="2026-09-19",
    issue_date="2026-09-22",
    charge="£100.00",
    allegation="Your vehicle was parked in a Parent and Child bay without being "
               "accompanied by a child",
    text="""NOTICE TO KEEPER
euro car parks
THIS PARKING CHARGE SERVES AS YOUR NOTICE TO KEEPER
Parking Charge Number: 45000564251
Vehicle Registration Mark: RX75VPP
Vehicle Make: PORSCHE
Date of Event: 19/09/2026
Date Issued: 22/09/2026
Payment Telephone Number: 020 3553 4559
PARKING CHARGE: £100.00
PAYMENT TO BE MADE WITHIN 28 DAYS OF DATE ISSUED: by 20/10/2026
This Parking Charge is discounted to £60.00 if paid within 14 days from the
date of issue by 06/10/2026
After this date, the FULL Parking Charge amount will be owed
Location: Sainsburys - Cromwell Road
Observation Time: 19/09/2026 12:23
Event Time: 19/09/2026 12:23
Contravention: Your vehicle was parked in a Parent and Child bay without being
accompanied by a child
Images timestamped 19/09/2026 12:24
Make your cheque payable to Euro Car Parks Limited, Euro Car Parks,
30 Dorset Square, London, NW1 6QJ.
""",
)

ECP_OVERSTAY = dict(
    case_id="R-ECP-OVERSTAY",
    operator="Euro Car Parks",
    ata="BPA",
    site="Sainsbury's - Abbey Wood",
    vrm="WR20YPH",
    event_date="2026-07-09",
    issue_date="2026-07-16",
    charge="£100.00",
    allegation="Your vehicle has overstayed the maximum time period allowed",
    text="""NOTICE TO KEEPER
euro car parks
THIS PARKING CHARGE SERVES AS YOUR NOTICE TO KEEPER
Parking Charge Number: 88812207965
Vehicle Registration Mark: WR20YPH
Vehicle Make: MERCEDES-BENZ
Date of Event: 09/07/2026
Date Issued: 16/07/2026
PARKING CHARGE: £100.00
PAYMENT TO BE MADE WITHIN 28 DAYS OF DATE ISSUED: by 13/08/2026
This Parking Charge is discounted to £60.00 If paid within 14 days from the
date of issue by 30/07/2026
Location: Sainsbury's - Abbey Wood
Time in Car Park: 5 hour(s) 35 minute(s)
Entry Time: 09/07/2026 13:42:50
Exit Time: 09/07/2026 19:17:18
Contravention: Your vehicle has overstayed the maximum time period allowed
Make your cheque payable to Euro Car Parks Limited, Euro Car Parks,
30 Dorset Square, London, NW1 6QJ.
""",
)

ECP_VOUCHER = dict(
    case_id="R-ECP-VOUCHER",
    operator="Euro Car Parks",
    ata="BPA",
    site="Sainsburys - Willesden Green",
    vrm="KJ19KYN",
    event_date="2026-08-29",
    issue_date="2026-09-04",
    charge="£100.00",
    allegation="A voucher/receipt was not validated at the kiosk during the time "
               "period the vehicle was on site",
    text="""NOTICE TO KEEPER
euro car parks
THIS PARKING CHARGE SERVES AS YOUR NOTICE TO KEEPER
Parking Charge Number: 88812545842
Vehicle Registration Mark: KJ19KYN
Vehicle Make: BMW
Date of Event: 29/08/2026
Date Issued: 04/09/2026
PARKING CHARGE: £100.00
PAYMENT TO BE MADE WITHIN 28 DAYS OF DATE ISSUED: by 02/10/2026
This Parking Charge is discounted to £60.00 If paid within 14 days from the
date of issue by 18/09/2026
Location: Sainsburys - Willesden Green
Time in Car Park: 1 hour(s) 9 minute(s)
Entry Time: 29/08/2026 13:05:22
Exit Time: 29/08/2026 14:14:31
Contravention: A voucher/receipt was not validated at the kiosk during the time
period the vehicle was on site
Make your cheque payable to Euro Car Parks Limited, Euro Car Parks,
30 Dorset Square, London, NW1 6QJ.
""",
)

APCOA_STANSTED = dict(
    case_id="R-APCOA-STANSTED",
    operator="APCOA Parking",
    ata="BPA",
    site="London Stansted Airport",
    vrm="BK71EXX",
    event_date="2026-05-15",
    issue_date="2026-05-22",
    charge="£100",
    allegation="Use of Pick Up / Drop Off Zone without making a valid payment",
    text="""PARKING CHARGE
APCOA PARKING
Parking Charge Number: STN1947529
Vehicle Registration Number: BK71EXX
Vehicle Make/Model: Tesla Model 3 Standard Range +
Date of Issue of this Charge: 22/05/2026
CONTRAVENTION DETAILS
Notice is hereby given to the Registered Keeper of vehicle registration mark:
BK71EXX
For the alleged contravention of: Use of Pick Up / Drop Off Zone without making
a valid payment
At: London Stansted Airport
Entry Time/Date: 12:59:17 15/05/2026
Exit Time/Date: 13:01:37 15/05/2026
The contravention is a BREACH OF THE TERMS AND CONDITIONS OF USE of the
facility. Signs are clearly displayed throughout the area showing these terms
and conditions. This Parking Charge was incurred on private land.
PARKING CHARGE AMOUNT: £100
PAYMENT TO BE MADE WITHIN 28 DAYS OF THE DATE ISSUED
This parking charge is discounted to £60 if paid within 14 days of the date
issued. After this date, the full parking charge amount will be owed.
A delay in payment beyond the payment period of 28 days may result in APCOA
instructing a Debt Collection agency to collect any sums due and/or APCOA may
also proceed with Court action against you.
APCOA, PO Box 5767, Dingwall, IV15 0AX
MEMBER OF THE BRITISH PARKING ASSOCIATION
""",
)

APCOA_LUTON = dict(
    case_id="R-APCOA-LUTON",
    operator="APCOA Parking (UK) Ltd",
    ata="BPA",
    site="Luton Airport Pick Up / Drop Off Zone",
    vrm="LT62PCO",
    event_date="2026-07-09",
    issue_date="2026-07-20",
    charge="£95",
    allegation="Use of Pick Up / Drop Off Zone without making a valid payment",
    text="""PARKING CHARGE
APCOA PARKING
Parking Charge Number: LU3489734
Vehicle Registration Number: LT62PCO
Vehicle Make/Model: Volkswagen Tiguan S Tdi Blue Tech 4m S-a
Date of Issue of this Charge: 20/07/2026
CONTRAVENTION DETAILS
Notice is hereby given to the Registered Keeper of vehicle registration mark:
LT62PCO
For the alleged contravention of: Use of Pick Up / Drop Off Zone without making
a valid payment
At: Luton Airport Pick Up / Drop Off Zone
Entry Time/Date: 03:54:00 09/07/2026
Exit Time/Date: 03:57:22 09/07/2026
The contravention is a BREACH OF THE TERMS AND CONDITIONS OF USE of the
facility. Signs are clearly displayed throughout the area showing these terms
and conditions. This Parking Charge was incurred on private land.
PARKING CHARGE AMOUNT: £95
PAYMENT TO BE MADE WITHIN 28 DAYS OF THE DATE ISSUED
This parking charge is discounted to £25 if paid within 14 days of the date
issued. After this date, the full parking charge amount will be owed.
APCOA, PO Box 5767, Dingwall, IV15 0AX
MEMBER OF THE BRITISH PARKING ASSOCIATION
""",
)

CPP_PERMIT_1 = dict(
    case_id="R-CPP-PERMIT-1",
    operator="Car Parking Partnership",
    ata="BPA",
    site="Charing Cross Hospital Underground Staff",
    vrm="KL18MTV",
    event_date="2026-07-10",
    issue_date="2026-07-10",
    charge="£100",
    allegation="No Valid Permit",
    # Windscreen notice: an attendant served it, so the driver was addressed,
    # not the keeper. Different liability route entirely.
    text="""CPP
CARPARKINGPARTNERSHIP
PARKING CHARGE NOTICE
Notice Number: 807295/905213
Date of Parking Event: 10/07/2026 10:43:05
Location: Charing Cross Hospital Underground Staff
Vehicle Registration: KL18MTV
Make: MERCEDES-BENZ
Model: unknown
Colour: GREY
Attendant Badge Number: MCSA7
Reason(s) for Issue
- No Valid Permit
Amount Due £100
Amount Due if paid within 14 days of the date of parking event (contravention) £60
Amount Due if NOT paid within 14 days of the date of parking event
(contravention) £100 + liability for further charges
In accordance with the terms and conditions set out in the signage, this is
private land, and the Parking Charge is now payable to Car Parking Partnership
(CPP) (as the Creditor).
There are 14 days during which you may pay the discounted amount as specified
above. Failure to do so will result in the full amount shown above becoming
payable. Any further delay in the payment may increase the charge and may
result in liability for further charges.
If this notice is not paid on or before the end of the 28 day period Car
Parking Partnership (CPP) may request the Registered Keepers details from the
Driver and Vehicle Licensing Agency (DVLA).
As the driver at the time of the parking event, you are now required to pay or
appeal the parking charge.
Car Parking Partnership (CPP) is a member of the Approved Operator Scheme and
the British Parking Association.
Car Parking Partnership, PO Box 117, Blyth, NE24 9EJ
""",
)

CPP_PERMIT_2 = dict(
    case_id="R-CPP-PERMIT-2",
    operator="Car Parking Partnership",
    ata="BPA",
    site="Charing Cross Hospital Underground Staff",
    vrm="KL18MTV",
    event_date="2026-09-08",
    issue_date="2026-09-08",
    charge="£100",
    allegation="No Valid Permit",
    # The same vehicle, site, attendant and reason as CPP_PERMIT_1, two months
    # later: a repeat charge against one keeper at their own workplace. The
    # header also shows "Parkingeye Ltd T/as Car Parking Partnership", which
    # is a creditor-identity question the notice itself raises.
    text="""Parkingeye Ltd T/as Car Parking Partnership (CPP), 40 Eaton...
CPP
CARPARKINGPARTNERSHIP
PARKING CHARGE NOTICE
Notice Number: 807291/105559
Date of Parking Event: 08/09/2026 10:43:16
Location: Charing Cross Hospital Underground Staff
Vehicle Registration: KL18MTV
Make: MERCEDES-BENZ
Model: unknown
Colour: GREY
Attendant Badge Number: MCSA7
Reason(s) for Issue
- No Valid Permit
Amount Due £100
Amount Due if paid within 14 days of the date of parking event (contravention) £60
Amount Due if NOT paid within 14 days of the date of parking event
(contravention) £100 + liability for further charges
In accordance with the terms and conditions set out in the signage, this is
private land, and the Parking Charge is now payable to Car Parking Partnership
(CPP) (as the Creditor).
As the driver at the time of the parking event, you are now required to pay or
appeal the parking charge.
Car Parking Partnership (CPP) is a member of the Approved Operator Scheme and
the British Parking Association.
To appeal against this Parking Charge please contact us within 28 days using
our online process, or by post.
Whilst an appeal is under assessment, the value of the charge will not
increase. If an appeal is unsuccessful, details to access the Independent
Appeals Service (POPLA) will be provided and 14 days to pay at the current
amount.
Car Parking Partnership, PO Box 117, Blyth, NE24 9EJ
""",
)

CPM_RESIDENTIAL = dict(
    case_id="R-CPM-RESIDENTIAL",
    operator="UK Car Park Management",
    ata="IPC",
    site="Weavers Quarter, Barking, IG11 7TS",
    vrm="LX26ZSE",
    event_date="2026-05-13",
    issue_date="2026-05-15",
    charge="£100.00",
    # Two alternative allegations in one line - the notice does not say which.
    allegation="Vehicle Not Registered Or Exceeded Allowed Time (ANPR)",
    text="""Parking Charge Notice
DO NOT IGNORE THIS NOTICE
CPM UK Car Park Management
Date: 15th May 2026
Parking Charge Details
Reference Number: 70377302
Vehicle Registration: LX26ZSE
Issued Date: 15th May 2026
Entry Time/Date: 17:42 13/05/2026
Exit Time/Date: 18:20 13/05/2026
Duration: 37 mins
Amount Due: Within 28 days £100.00
Payment for the Parking Charge 70377302 is due. Please pay the reduced charge
of £60.00 now.
We have issued Parking Charge 70377302 to your vehicle because on the 13th May
2026 at 18:20 it was parked on private land in breach of the terms and
conditions of parking at Weavers Quarter, Barking, IG11 7TS, making the driver
liable for a Parking Charge. As the PCN has not been paid in full, the Parking
Charge remains outstanding.
The reason we issued the Parking Charge to the vehicle is as follows: Vehicle
Not Registered Or Exceeded Allowed Time (ANPR).
The signage, which is clearly displayed throughout the area, states the land is
private and that parking conditions apply. By parking on site the driver is
bound by these terms and conditions and liable to pay a charge if they are not
adhered to. The signage also confirms that the area is managed by UK Car Park
Management (CPM).
We, the Creditor, now require this amount to be paid using one of the payment
methods described overleaf. If you were not the driver of the vehicle, you
should notify us in writing (see reverse for details) of the name of the driver
and a current address for service for the driver. You should also pass this
notice on to the driver.
A discounted charge of £60.00 applies if this Parking Charge Notice is paid
within 14 days from the Issued Date. If you choose not to pay at this amount,
the full value of £100.00 will be due. The Registered Keeper details of this
vehicle have been requested from the DVLA through the reasonable cause criteria
of pursuing an outstanding Parking Charge.
You are advised that if, after the period of 28 days beginning with the day
after that on which this notice is given - the amount of the unpaid parking
charge specified in this notice has not been paid in full, and we do not know
both the name and current address of the driver, under paragraph 9(2)(f) of
Schedule 4 of the Protection of Freedoms Act 2012 we will have the right to
recover from the keeper so much of that parking charge amount as remains
unpaid. If we are required to take further action to recover this Parking
Charge the amount due may increase to up to £170.00.
Operating in accordance with the International Parking Community's Code of
Practice.
Payments & Collections, UK-CPM, PO Box 3114, Lancing, BN15 5BR
Made payable to UK Car Park Management Ltd
""",
)

REAL_NOTICES = [
    ECP_PARENT_CHILD, ECP_OVERSTAY, ECP_VOUCHER,
    APCOA_STANSTED, APCOA_LUTON,
    CPP_PERMIT_1, CPP_PERMIT_2,
    CPM_RESIDENTIAL,
]

__all__ = ["REAL_NOTICES"] + [n["case_id"].replace("-", "_") for n in REAL_NOTICES]
