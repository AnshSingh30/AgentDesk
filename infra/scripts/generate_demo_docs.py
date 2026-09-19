"""Generate fictional demo documents (PRD §22.3): Northwind Tech (HR) and Greenwood Public School.

All content is invented. PDFs use fpdf2 with headings, paragraphs and tables so the
layout-aware parser has something to chew on. Facts match the template eval sets.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

from fpdf import FPDF

ROOT = Path(__file__).resolve().parents[2]
DEMO = ROOT / "demo"


class Doc(FPDF):
    def __init__(self, title: str, org: str) -> None:
        super().__init__()
        self.doc_title = title
        self.org = org
        self.set_auto_page_break(auto=True, margin=18)
        self.set_title(title)
        self.set_creation_date(datetime(2026, 1, 1, tzinfo=UTC))  # reproducible output

    def header(self) -> None:  # noqa: D102
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120)
        self.cell(0, 6, f"{self.org} - {self.doc_title}", align="L")
        self.ln(8)
        self.set_text_color(0)

    def footer(self) -> None:  # noqa: D102
        self.set_y(-12)
        self.set_font("Helvetica", "I", 8)
        self.set_text_color(120)
        self.cell(0, 6, f"Page {self.page_no()} - Confidential, for internal use", align="C")
        self.set_text_color(0)

    def h1(self, text: str) -> None:
        self.set_font("Helvetica", "B", 18)
        self.multi_cell(0, 9, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def h2(self, text: str) -> None:
        self.set_font("Helvetica", "B", 13)
        self.multi_cell(0, 8, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(1)

    def p(self, text: str) -> None:
        self.set_font("Helvetica", "", 10.5)
        self.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def bullets(self, items: list[str]) -> None:
        self.set_font("Helvetica", "", 10.5)
        for it in items:
            self.multi_cell(0, 5.5, f"  - {it}", new_x="LMARGIN", new_y="NEXT")
        self.ln(2)

    def tbl(self, rows: list[list[str]], widths: list[int] | None = None) -> None:
        self.set_font("Helvetica", "", 9.5)
        with FPDF.table(self, col_widths=widths, text_align="LEFT", line_height=6) as table:
            for row in rows:
                r = table.row()
                for cell in row:
                    r.cell(cell)
        self.ln(3)


def two_columns(pdf: FPDF, text: str) -> None:
    try:
        with pdf.text_columns(ncols=2, gutter=8, text_align="J") as cols:
            pdf.set_font("Helvetica", "", 10)
            cols.write(text)
    except Exception:  # noqa: BLE001 - older fpdf2
        pdf.set_font("Helvetica", "", 10)
        pdf.multi_cell(0, 5.5, text, new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)


# ---------------------------------------------------------------- Northwind Tech
NW = "Northwind Tech"


def northwind_handbook(out: Path) -> None:
    pdf = Doc("Employee Handbook 2026", NW)
    pdf.add_page()
    pdf.h1("Northwind Tech Employee Handbook")
    pdf.p(
        "Welcome to Northwind Tech. This handbook explains how we work, what you can expect from us and what we expect from you. "
        "It applies to all full-time, part-time and contract employees across our Bengaluru, Pune, Hyderabad and Gurugram offices. "
        "Where a policy document is more detailed than this handbook, the policy document prevails."
    )
    pdf.h2("1. About Northwind Tech")
    pdf.p(
        "Northwind Tech builds supply-chain analytics software for mid-sized manufacturers. Founded in 2014, we now employ "
        "around 500 people across Engineering, Product, Sales, Customer Success, Marketing, Finance, People Operations and IT. "
        "Our values are Customer Obsession, Ownership, Candour and Craft. We are a hybrid company: most teams work from the "
        "office three days a week and from home two days a week. Every employee is assigned a buddy for their first 60 days "
        "and a manager who holds weekly one-to-ones. Performance is reviewed twice a year in April and October."
    )
    pdf.h2("2. Working hours and attendance")
    pdf.p(
        "Core hours are 10:00 to 16:00 IST. Standard working time is 40 hours per week, Monday to Friday. Employees record attendance "
        "in the People Portal; work-from-home days are recorded as WFH. Repeated unrecorded absence is treated as unauthorised leave."
    )
    pdf.h2("3. Probation, notice period and exit")
    pdf.p(
        "New joiners are on probation for 90 days. The notice period is 60 days for all employees after probation and 15 days during "
        "probation. Notice must be given in writing through the People Portal. The last working day is agreed with your manager, and "
        "all company equipment must be returned to IT on or before that day. Final settlement is paid within 30 days of exit."
    )
    pdf.h2("4. Leave overview")
    pdf.p(
        "Northwind offers casual leave, sick leave, earned leave, maternity and paternity leave, and bereavement leave. Full details, "
        "entitlements and the carry-forward rules are in the Leave Policy. In summary, employees receive 12 casual leaves per year, "
        "8 sick leaves per year and 15 earned leaves per year."
    )
    pdf.h2("5. Hybrid work")
    pdf.p(
        "Employees may work from home up to 2 days per week with manager approval, as set out in the WFH & Travel Policy. Fully remote "
        "arrangements require written approval from the department head and People Operations."
    )
    pdf.h2("6. Compensation and payroll")
    pdf.p(
        "Salaries are paid on the last working day of each month into the bank account registered in the People Portal. Payslips are "
        "available in the portal. Tax declarations are collected in April and proofs in January. Questions about payroll go to "
        "payroll@northwind.example."
    )
    pdf.h2("7. Benefits overview")
    pdf.p(
        "All employees are covered by group health insurance of Rs. 5,00,000 per family per year, group term life insurance of 3x annual "
        "CTC, and a Provident Fund contribution as per statute. See the Benefits Summary for details, including the wellness allowance."
    )
    pdf.h2("8. Code of conduct")
    pdf.p(
        "We expect honesty, respect and professionalism. Harassment, discrimination and retaliation are not tolerated. Concerns can be "
        "raised confidentially with People Operations (hr@northwind.example) or through the anonymous Speak Up line. See the Code of "
        "Conduct for the full policy."
    )
    pdf.h2("9. Information security")
    pdf.p(
        "Use your Northwind laptop for all work. Enable full-disk encryption and the company VPN. Never share passwords. Report lost "
        "devices to it-helpdesk@northwind.example within 2 hours. Customer data may not be copied to personal devices or personal cloud "
        "storage."
    )
    pdf.h2("10. Learning and development")
    pdf.p(
        "Each employee has an annual learning budget of Rs. 40,000 for courses, books and conferences, approved by their manager. "
        "Northwind also runs an internal engineering academy every quarter."
    )
    pdf.h2("11. First-week checklist for new joiners")
    pdf.bullets(
        [
            "Day 1: collect your laptop and ID card from IT at the reception desk; complete e-signature of the offer and NDA.",
            "Day 1: set up email, Slack, the People Portal and the VPN using the IT Onboarding Guide.",
            "Day 2: meet your buddy and manager; review your 30-60-90 day plan.",
            "Day 3: complete the mandatory Code of Conduct and Information Security e-learning (60 minutes).",
            "Day 4: submit bank details, PAN and tax declaration in the People Portal.",
            "Day 5: attend the Northwind Welcome session (every Friday, 11:00, Town Hall room or Zoom).",
        ]
    )
    pdf.h2("12. Useful contacts")
    pdf.tbl(
        [
            ["Topic", "Contact", "Hours"],
            ["HR / People Operations", "hr@northwind.example", "Mon-Fri 09:30-18:00"],
            ["IT helpdesk", "it-helpdesk@northwind.example", "Mon-Sat 08:00-20:00"],
            ["Payroll", "payroll@northwind.example", "Mon-Fri 10:00-17:00"],
            ["Facilities", "facilities@northwind.example", "Mon-Fri 08:00-19:00"],
            ["Speak Up line (anonymous)", "speakup.northwind.example", "24x7"],
        ],
        [55, 75, 55],
    )
    for n in range(13, 26):
        pdf.h2(
            f"{n}. Appendix {n - 12}: {['Office locations', 'Meeting room etiquette', 'Expense categories', 'Travel classes', 'Asset tagging', 'Data classification', 'Incident reporting', 'Grievance process', 'Recognition programme', 'Referral bonus', 'Sabbatical policy', 'Alumni network', 'Glossary'][n - 13]}"
        )
        pdf.p(
            "This appendix provides supporting detail for the section referenced above. Where an appendix conflicts with a policy "
            "document, the policy document prevails. Appendices are reviewed annually by People Operations and Legal. "
            "For questions contact hr@northwind.example. " * 3
        )
    pdf.output(str(out))


def northwind_leave_policy(out: Path) -> None:
    pdf = Doc("Leave Policy v3.2", NW)
    pdf.add_page()
    pdf.h1("Leave Policy")
    pdf.p(
        "Effective 1 January 2026. Applies to all regular employees. Leave is tracked in the People Portal and approved by the reporting manager."
    )
    pdf.h2("1. Leave entitlements")
    pdf.tbl(
        [
            ["Leave type", "Entitlement per year", "Carry forward", "Notes"],
            [
                "Casual leave (CL)",
                "12 days",
                "No - lapses on 31 December",
                "Max 3 consecutive days; apply at least 1 day ahead",
            ],
            [
                "Sick leave (SL)",
                "8 days",
                "No",
                "Medical certificate required for more than 2 consecutive days",
            ],
            ["Earned leave (EL)", "15 days", "Yes - up to 30 days", "Encashable at exit; apply 7 days ahead"],
            ["Maternity leave", "26 weeks", "n/a", "As per the Maternity Benefit Act"],
            ["Paternity leave", "10 working days", "No", "Within 3 months of birth or adoption"],
            ["Bereavement leave", "5 days", "No", "Immediate family"],
            ["Marriage leave", "5 days", "No", "Once during employment"],
        ],
        [38, 38, 42, 67],
    )
    pdf.h2("2. Casual leave")
    pdf.p(
        "Employees get 12 casual leaves per calendar year, credited at 1 day per month. Casual leave cannot be carried forward to the "
        "next year and is not encashable. Casual leave may be taken as half days. Unplanned casual leave must be informed to the manager "
        "before 10:00 on the day of absence."
    )
    pdf.h2("3. Sick leave")
    pdf.p(
        "Employees get 8 sick leaves per year. A medical certificate is required for absences of more than 2 consecutive days. Unused sick "
        "leave lapses at year end."
    )
    pdf.h2("4. Earned leave")
    pdf.p(
        "Employees accrue 15 earned leaves per year (1.25 days per month). Up to 30 days can be carried forward. Earned leave beyond the "
        "carry-forward cap lapses. Earned leave balance is encashed at exit at basic salary rate."
    )
    pdf.h2("5. Public holidays")
    pdf.p(
        "Northwind observes 12 public holidays per year, published in the Holiday Calendar. Two additional optional (restricted) holidays "
        "may be chosen from the restricted list."
    )
    pdf.h2("6. How to apply")
    pdf.bullets(
        [
            "Open the People Portal > Leave > Apply.",
            "Choose the leave type and dates; half days are supported for CL and EL.",
            "Your manager approves within 2 working days; escalate to HR if no response.",
            "Leave without balance is treated as loss of pay unless approved by the department head.",
        ]
    )
    pdf.h2("7. Contacts")
    pdf.p("Questions about leave balances go to hr@northwind.example.")
    pdf.output(str(out))


def northwind_wfh(out: Path) -> None:
    pdf = Doc("WFH & Travel Policy", NW)
    pdf.add_page()
    pdf.h1("Work From Home & Travel Policy")
    pdf.h2("1. Work from home")
    pdf.p(
        "Employees may work from home up to 2 days per week. WFH days are agreed with the manager at the start of each month and recorded "
        "in the People Portal under Attendance > WFH. Teams must keep Tuesday as a common office day. WFH requests for more than 2 days in a "
        "week need department head approval. During WFH, employees must be reachable on Slack during core hours (10:00-16:00) and use the VPN."
    )
    pdf.h2("2. Equipment for remote work")
    pdf.p(
        "A one-time home office allowance of Rs. 15,000 is available after probation for a desk, chair or monitor. Claims are submitted via "
        "the Expenses app with invoices."
    )
    pdf.h2("3. Business travel")
    pdf.tbl(
        [
            ["Grade", "Domestic air", "Hotel cap per night", "Per diem"],
            ["L1-L3", "Economy", "Rs. 4,500", "Rs. 1,500"],
            ["L4-L5", "Economy", "Rs. 6,500", "Rs. 2,000"],
            ["L6+", "Premium economy", "Rs. 9,000", "Rs. 2,500"],
        ],
        [40, 45, 50, 45],
    )
    pdf.p(
        "Travel must be booked through the Travel Desk (travel@northwind.example) at least 5 working days in advance. Expenses are "
        "reimbursed within 15 days of submission with receipts."
    )
    pdf.output(str(out))


def northwind_benefits(out: Path) -> None:
    pdf = Doc("Benefits Summary 2026", NW)
    pdf.add_page()
    pdf.h1("Benefits Summary")
    pdf.tbl(
        [
            ["Benefit", "Cover / amount", "Who is covered"],
            ["Group health insurance", "Rs. 5,00,000 per year", "Employee, spouse, up to 2 children"],
            ["Parental cover (optional top-up)", "Rs. 3,00,000", "Parents or parents-in-law"],
            ["Group term life", "3x annual CTC", "Employee"],
            ["Accident insurance", "Rs. 25,00,000", "Employee"],
            ["Wellness allowance", "Rs. 12,000 per year", "Employee"],
            ["Learning budget", "Rs. 40,000 per year", "Employee"],
            ["Meal card", "Rs. 2,200 per month", "Employee"],
        ],
        [60, 55, 65],
    )
    pdf.h2("How to claim health insurance")
    pdf.p(
        "Cashless treatment is available at 6,000+ network hospitals using the insurance e-card in the Benefits app. For reimbursement "
        "claims, submit bills within 30 days of discharge. The insurance helpline is 1800-000-1234."
    )
    pdf.output(str(out))


def northwind_holidays(out: Path) -> None:
    pdf = Doc("Holiday Calendar 2026", NW)
    pdf.add_page()
    pdf.h1("Holiday Calendar 2026")
    pdf.p("Northwind Tech observes the following 12 public holidays in 2026 across all India offices.")
    pdf.tbl(
        [
            ["Date", "Day", "Holiday"],
            ["1 January 2026", "Thursday", "New Year's Day"],
            ["14 January 2026", "Wednesday", "Makar Sankranti / Pongal"],
            ["26 January 2026", "Monday", "Republic Day"],
            ["4 March 2026", "Wednesday", "Holi"],
            ["1 May 2026", "Friday", "May Day"],
            ["15 August 2026", "Saturday", "Independence Day"],
            ["4 September 2026", "Friday", "Ganesh Chaturthi"],
            ["2 October 2026", "Friday", "Gandhi Jayanti"],
            ["20 October 2026", "Tuesday", "Dussehra"],
            ["9 November 2026", "Monday", "Diwali"],
            ["10 November 2026", "Tuesday", "Diwali (day 2)"],
            ["25 December 2026", "Friday", "Christmas"],
        ],
        [50, 40, 90],
    )
    pdf.h2("Restricted (optional) holidays")
    pdf.p(
        "Choose any 2: Ugadi (19 March), Good Friday (3 April), Eid al-Fitr (21 March), Raksha Bandhan (28 August), Onam (26 August), Guru Nanak Jayanti (24 November)."
    )
    pdf.output(str(out))


NW_CODE_OF_CONDUCT = """# Northwind Tech Code of Conduct

## Our commitments

We treat everyone with respect. We do not tolerate harassment, bullying, discrimination or retaliation of any kind.

## Conflicts of interest

Disclose any outside employment, board seats or investments in customers, suppliers or competitors to People Operations.

## Gifts and hospitality

Gifts above Rs. 2,000 from suppliers or customers must be declined or declared to your manager.

## Confidentiality

Customer and company information is confidential during and after employment. Do not discuss deals, roadmaps or personnel matters outside the company.

## Raising concerns

Contact hr@northwind.example, your manager, or the anonymous Speak Up line at speakup.northwind.example. Reports are investigated within 10 working days.

## Consequences

Breaches may lead to disciplinary action up to termination.
"""

NW_IT_GUIDE = """# IT Onboarding Guide

## Day 1 setup

1. Collect your laptop and ID card at the reception desk (IT counter, ground floor).
2. Sign in to your Northwind Google account using the temporary password from the welcome email. You will be asked to enable 2-factor authentication.
3. Install Slack, Zoom and the Northwind VPN client from the Self Service app.
4. Enable FileVault / BitLocker full-disk encryption (required within 24 hours).

## Getting help

- Laptop, VPN or account issues: it-helpdesk@northwind.example or the #it-helpdesk Slack channel. Hours: Mon-Sat 08:00-20:00.
- Lost or stolen device: report within 2 hours to it-helpdesk@northwind.example and call +91 80 4000 1234.

## Access requests

Request access to GitHub, Jira and production systems through the Access Portal. Manager approval is automatic for team defaults; production access needs the security team.

## Passwords

Use the company password manager (1Password). Never reuse personal passwords. Passwords must be at least 14 characters.
"""


# ---------------------------------------------------------------- Greenwood Public School
GW = "Greenwood Public School"


def greenwood_parent_handbook(out: Path) -> None:
    pdf = Doc("Parent Handbook 2026-27", GW)
    pdf.add_page()
    pdf.h1("Parent Handbook 2026-27")
    pdf.p(
        "Greenwood Public School, Gomti Nagar Extension, Lucknow. CBSE affiliated, classes Nursery to XII. This handbook covers timings, "
        "communication, attendance, discipline and contacts."
    )
    pdf.h2("1. School timings")
    pdf.tbl(
        [
            ["Classes", "Timings (Mon-Fri)", "Saturday"],
            ["Nursery - KG", "8:30 AM - 12:00 PM", "Closed"],
            ["Class 1 - 5", "8:00 AM - 2:00 PM", "8:00 AM - 12:00 PM (1st & 3rd Saturday)"],
            ["Class 6 - 12", "7:45 AM - 2:15 PM", "7:45 AM - 12:30 PM (1st & 3rd Saturday)"],
        ],
        [45, 60, 75],
    )
    pdf.p(
        "Gates open 20 minutes before start time. Students arriving more than 15 minutes late need a late slip from the front office."
    )
    pdf.h2("2. Office hours and contacts")
    pdf.tbl(
        [
            ["Office", "Contact", "Hours"],
            [
                "Front office / general",
                "office@greenwood.example, 0522-400-1000",
                "Mon-Sat 8:00 AM - 3:00 PM",
            ],
            ["Accounts (fees)", "accounts@greenwood.example, 0522-400-1002", "Mon-Fri 9:00 AM - 2:00 PM"],
            ["Admissions", "admissions@greenwood.example, 0522-400-1003", "Mon-Sat 9:00 AM - 1:00 PM"],
            ["Transport in-charge", "transport@greenwood.example, 98390-00123", "Mon-Sat 7:00 AM - 4:00 PM"],
            ["Emergency (school nurse)", "0522-400-1010", "During school hours"],
        ],
        [50, 75, 55],
    )
    pdf.h2("3. Attendance and leave")
    pdf.p(
        "A minimum of 75% attendance is required to sit for final examinations. Leave applications must be submitted through the parent "
        "app or in the school diary. For sickness of more than 3 days, attach a medical certificate."
    )
    pdf.h2("4. Communication")
    pdf.p(
        "Circulars are sent through the Greenwood parent app and SMS. Parent-teacher meetings are held on the second Saturday of every "
        "month from 9:00 AM to 12:00 PM. Teachers do not share personal phone numbers; use the app messaging."
    )
    pdf.h2("5. Individual student matters")
    pdf.p(
        "For questions about a specific child's marks, attendance or conduct, please contact the class teacher through the parent app or "
        "visit the front office (Mon-Sat 8:00 AM - 3:00 PM). Fee disputes are handled by the Accounts office."
    )
    pdf.h2("6. Health and safety")
    pdf.p(
        "In an emergency during school hours call the school office at 0522-400-1000 immediately. A nurse is on duty from 7:30 AM to 3:00 PM. "
        "Please keep the medical information in the parent app up to date."
    )
    pdf.output(str(out))


def greenwood_fees(out: Path) -> None:
    pdf = Doc("Fee Structure 2026-27", GW)
    pdf.add_page()
    pdf.h1("Fee Structure 2026-27")
    pdf.h2("1. Annual fees by class")
    pdf.tbl(
        [
            ["Class", "Tuition fee (annual)", "Annual charges", "Total"],
            ["Nursery - KG", "Rs. 54,000", "Rs. 8,000", "Rs. 62,000"],
            ["Class 1 - 5", "Rs. 66,000", "Rs. 9,000", "Rs. 75,000"],
            ["Class 6 - 8", "Rs. 78,000", "Rs. 10,000", "Rs. 88,000"],
            ["Class 9 - 10", "Rs. 90,000", "Rs. 11,000", "Rs. 1,01,000"],
            ["Class 11 - 12 (Science)", "Rs. 1,08,000", "Rs. 12,000", "Rs. 1,20,000"],
            ["Class 11 - 12 (Commerce/Humanities)", "Rs. 96,000", "Rs. 12,000", "Rs. 1,08,000"],
        ],
        [60, 45, 40, 35],
    )
    pdf.h2("2. Payment schedule")
    pdf.tbl(
        [
            ["Instalment", "Period covered", "Due date", "Last date without late fee"],
            ["Q1", "April - June", "1 April 2026", "15 April 2026"],
            ["Q2", "July - September", "1 July 2026", "15 July 2026"],
            ["Q3", "October - December", "1 October 2026", "15 December 2026"],
            ["Q4", "January - March", "1 January 2027", "15 January 2027"],
        ],
        [30, 50, 45, 55],
    )
    pdf.p(
        "Q3 fee payment: the last date for Q3 (October to December) fee payment without late fee is 15 December 2026. "
        "A late fee of Rs. 50 per day applies after the last date. Fees can be paid online through the parent app or at the Accounts office."
    )
    pdf.h2("3. Other charges")
    pdf.tbl(
        [
            ["Item", "Amount"],
            ["Transport (per quarter)", "Rs. 6,000 - Rs. 9,000 depending on route"],
            ["Admission fee (one-time)", "Rs. 25,000"],
            ["Caution deposit (refundable)", "Rs. 10,000"],
        ],
        [80, 100],
    )
    pdf.output(str(out))


def greenwood_calendar(out: Path) -> None:
    pdf = Doc("Academic Calendar 2026-27", GW)
    pdf.add_page()
    pdf.h1("Academic Calendar 2026-27")
    pdf.h2("Term dates")
    pdf.tbl(
        [
            ["Event", "Dates"],
            ["Session begins", "1 April 2026"],
            ["Summer vacation", "20 May - 30 June 2026"],
            ["Term 1 examinations", "14 - 25 September 2026"],
            ["Autumn break", "19 - 23 October 2026"],
            ["Winter vacation", "24 December 2026 - 3 January 2027"],
            ["Term 2 examinations", "22 February - 8 March 2027"],
            ["Result day", "25 March 2027"],
        ],
        [80, 100],
    )
    pdf.h2("Holidays 2026-27")
    pdf.tbl(
        [
            ["Date", "Holiday", "School status"],
            ["15 August 2026", "Independence Day (flag hoisting 8:00 AM)", "Closed after assembly"],
            ["2 October 2026", "Gandhi Jayanti", "Closed - school is closed on 2 October"],
            ["20 October 2026", "Dussehra", "Closed"],
            ["9 - 10 November 2026", "Diwali", "Closed"],
            ["25 December 2026", "Christmas", "Closed (winter vacation)"],
            ["26 January 2027", "Republic Day", "Closed after assembly"],
            ["4 March 2027", "Holi", "Closed"],
        ],
        [45, 85, 50],
    )
    pdf.h2("Events")
    pdf.p(
        "Annual Sports Day: 12 December 2026. Annual Day: 20 February 2027. Science exhibition: 8 August 2026. Parent-teacher meetings: second Saturday of every month."
    )
    pdf.output(str(out))


def greenwood_transport(out: Path) -> None:
    pdf = Doc("Transport Routes 2026-27", GW)
    pdf.add_page()
    pdf.h1("Transport Routes 2026-27")
    pdf.p(
        "School buses run on 6 routes. Pick-up times are approximate; buses wait a maximum of 2 minutes at each stop. Contact the transport "
        "in-charge at transport@greenwood.example or 98390-00123."
    )
    pdf.tbl(
        [
            ["Route", "Areas covered", "First pick-up", "Quarterly fee"],
            ["Route 1", "Hazratganj, Lalbagh, Aminabad", "6:50 AM", "Rs. 9,000"],
            ["Route 2", "Indira Nagar, Munshi Pulia, Lekhraj", "7:00 AM", "Rs. 7,500"],
            ["Route 3", "Gomti Nagar, Vibhuti Khand, Vipul Khand, Patrakarpuram", "7:05 AM", "Rs. 6,000"],
            ["Route 4", "Aliganj, Mahanagar, Kapoorthala", "6:55 AM", "Rs. 8,000"],
            ["Route 5", "Sushant Golf City, Shaheed Path, Arjunganj", "7:00 AM", "Rs. 7,500"],
            ["Route 6", "Chinhat, Kamta, Matiyari", "6:50 AM", "Rs. 8,500"],
        ],
        [25, 90, 30, 35],
    )
    pdf.p(
        "Gomti Nagar is covered by Route 3. Route changes need a written request to the transport office 15 days before the start of a quarter."
    )
    pdf.output(str(out))


GW_ADMISSIONS = """# Admissions Guide 2026-27

## Classes and age criteria

| Class | Minimum age on 31 March 2026 |
|---|---|
| Nursery | 3 years |
| KG | 4 years |
| Class 1 | 5 years 6 months |

## Documents required for admission to Class 1

- Birth certificate (original and one copy)
- Two passport-size photographs of the child and one of each parent
- Aadhaar card copy of the child and parents
- Proof of residence (electricity bill, rent agreement or passport)
- Transfer certificate from the previous school (if applicable)
- Immunisation record

## Process

1. Register online on the school website or at the Admissions office (Mon-Sat 9:00 AM - 1:00 PM).
2. Interaction with the child and parents (no written test up to Class 1).
3. Offer letter within 7 working days; admission fee of Rs. 25,000 payable within 10 days to confirm the seat.

## Contact

admissions@greenwood.example, 0522-400-1003
"""

GW_UNIFORM = """# Uniform Policy

## Regular uniform (Monday, Tuesday, Thursday, Friday)

- Boys: white shirt, grey trousers, school tie, black shoes, grey socks.
- Girls: white shirt, grey pinafore (Class 1-5) or grey skirt (Class 6-12), school tie, black shoes, grey socks.

## Sports uniform (Wednesday and Saturday)

House T-shirt (red, blue, green or yellow), navy track pants, white sports shoes.

## Winter (November to February)

Navy blazer or navy sweater with the school crest. Navy caps and mufflers are allowed.

## Where to buy

Authorised vendor: Greenwood Uniform Store, next to Gate 2, open Mon-Sat 8:00 AM - 5:00 PM.
"""


def generate_all(base: Path = DEMO) -> dict[str, list[Path]]:
    nw = base / "northwind"
    gw = base / "greenwood"
    nw.mkdir(parents=True, exist_ok=True)
    gw.mkdir(parents=True, exist_ok=True)
    northwind_handbook(nw / "Employee Handbook.pdf")
    northwind_leave_policy(nw / "Leave Policy.pdf")
    northwind_wfh(nw / "WFH and Travel Policy.pdf")
    northwind_benefits(nw / "Benefits Summary.pdf")
    northwind_holidays(nw / "Holiday Calendar 2026.pdf")
    (nw / "Code of Conduct.md").write_text(NW_CODE_OF_CONDUCT)
    (nw / "IT Onboarding Guide.md").write_text(NW_IT_GUIDE)
    greenwood_parent_handbook(gw / "Parent Handbook.pdf")
    greenwood_fees(gw / "Fee Structure 2026-27.pdf")
    greenwood_calendar(gw / "Academic Calendar.pdf")
    greenwood_transport(gw / "Transport Routes.pdf")
    (gw / "Admissions Guide.md").write_text(GW_ADMISSIONS)
    (gw / "Uniform Policy.md").write_text(GW_UNIFORM)
    return {"northwind": sorted(nw.iterdir()), "greenwood": sorted(gw.iterdir())}


if __name__ == "__main__":
    out = generate_all()
    for org, files in out.items():
        print(org, [f.name for f in files])
    sys.exit(0)
