"""Seed thirteen completed interview sessions with full transcripts and approved
scoring rubrics, for exercising automated scoring by hand in the Candidates section.

Each candidate lands in exactly the state a live interview reaches right before the
judge runs: a completed session, a full transcript, and an approved rubric - but no
Evaluation row yet, since scoring only fires automatically off the real join/end path.
Open the Candidates page and press "Run scoring" on each one to exercise the real judge
end to end, rather than read back canned output.

The first three are the clean tiers - strong fit, decent fit, not a fit - so the
assessment page, rubric breakdown and evidence citations can be checked against an
unambiguous spread. The remaining ten are the awkward cases, each written to probe one
specific way the judge can go wrong:

  Siti Rahmawati    strong background, weak reasoning - do the dimensions split, or
                    does an impressive CV pull the whole verdict up?
  Marcus Tan        thin background, excellent reasoning - the mirror image, and a test
                    that being upfront about a gap is not itself scored as a gap.
  Rizky Pratama     ASR-degraded transcript - must reach Inconclusive through the
                    transcript-quality gate, never Not a Fit.
  Nadia Halim       session abandoned after two turns - almost nothing evidenced, so
                    dimensions should come back not_evidenced rather than judged on
                    scraps.
  Bagus Santoso     fluent, warm and entirely content-free - fluency must not read as
                    substance, and the evidence citations should come up empty.
  Yulianti Saragih  strong engineer, broken English - the clarity dimension has to
                    score meaning conveyed, not grammar. A Not a Fit here is a bug.
  Priya Menon       rehearsed framework answers that never touch the question asked,
                    and collapse under three direct follow-ups.
  Andre Wijaya      inflated claims that contradict themselves across turns - the judge
                    has to notice the conflict rather than quote the first version.
  Hendra Lim        deep reliability experience in cement, none in edible oils, against
                    a rubric that explicitly allows transferable process experience.
  Clarissa Wong     repeatedly probes the guardrail topics - the verdict must rest on
                    her answers, not penalise her for asking.

    cd backend && .venv/Scripts/python scripts/seed_sample_candidates.py
"""

import asyncio
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession  # noqa: E402

from app.core.db import SessionLocal, init_db  # noqa: E402
from app.core.security import new_join_token  # noqa: E402
from app.models import (  # noqa: E402
    DIMENSION_KEYS,
    Interview,
    InterviewSession,
    Rubric,
    RubricDimension,
    TranscriptTurn,
)

CANDIDATES: list[dict] = [
    # ---------------------------------------------------------- expected: strong fit
    {
        "interview_title": "Supply Chain Analyst - Q3 Intake",
        "position_title": "Regional Supply Chain Analyst",
        "guardrail_notes": "Do not discuss relocation or compensation packages.",
        "candidate_name": "Amara Fernandez",
        "candidate_email": "amara.fernandez@example.com",
        "days_ago": 1,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has hands-on demand planning or supply chain analytics "
                    "experience in FMCG or agribusiness, names specific systems (SQL, "
                    "Power BI, SAP/APO) and describes concrete forecasting or S&OP work "
                    "at a comparable regional scale."
                ),
                "decent": (
                    "Candidate has adjacent analytics or operations experience and some "
                    "exposure to relevant tools, but the scope is narrower - fewer SKUs, "
                    "a shorter tenure, or a different industry without direct "
                    "agribusiness/FMCG exposure."
                ),
                "not_fit": (
                    "Candidate has no meaningful forecasting, planning or supply-chain-"
                    "adjacent experience, or cannot describe any concrete tool or system "
                    "they have used for this kind of work."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate lays out a clear, prioritised approach to a supply "
                    "disruption scenario - quantifying the impact, considering "
                    "reallocation or expediting options, and looping in the right "
                    "stakeholders - without needing heavy prompting."
                ),
                "decent": (
                    "Candidate identifies some reasonable steps but the approach is "
                    "incomplete or generic, or needs significant prompting to get past "
                    "a surface-level answer."
                ),
                "not_fit": (
                    "Candidate cannot propose a coherent approach to the scenario, or "
                    "the answer is unrelated to the situation posed."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate explains technical work in plain, structured language a "
                    "non-technical stakeholder could follow, leading with business "
                    "impact before mechanics."
                ),
                "decent": (
                    "Candidate's explanations are understandable but sometimes get lost "
                    "in technical detail or lack a clear structure."
                ),
                "not_fit": (
                    "Candidate's answers are difficult to follow, contradictory, or too "
                    "vague to convey what they actually did or think."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Amara, thanks for joining today. I'm the AI interviewer for the "
                "Regional Supply Chain Analyst role. This will be a relaxed "
                "conversation, about fifteen minutes, just to get to know your "
                "background and how you think through a few scenarios. Ready to start "
                "with a quick introduction?",
            ),
            (
                "candidate",
                "Yes, ready. Hi, thanks for having me. I'm Amara Fernandez, I've spent "
                "the last four years as a demand planner at a regional FMCG "
                "distributor based in Jakarta, covering about forty SKUs of packaged "
                "food across Indonesia, Malaysia and Vietnam. Before that I did two "
                "years in a junior analyst role in inventory control. My day to day is "
                "building monthly demand forecasts, running S&OP meetings with "
                "commercial and ops, and maintaining our planning dashboards.",
            ),
            (
                "ai",
                "That's helpful context. Can you tell me more about the tools and "
                "systems you use day to day, especially anything relevant to SQL, "
                "Power BI or SAP?",
            ),
            (
                "candidate",
                "Sure. I write most of my forecasting queries directly in SQL against "
                "our data warehouse, joining sales history, the promotions calendar "
                "and shipment data. I built our regional Power BI dashboard from "
                "scratch, it's what the whole planning team uses now for weekly "
                "reviews. We use SAP APO for the actual planning runs, so I'm "
                "comfortable navigating planning books and adjusting statistical "
                "forecasts there, though the heavier customisation is handled by our "
                "IT team.",
            ),
            (
                "ai",
                "Good. Now a scenario: imagine a key vessel shipment of refined palm "
                "oil bound for the Jakarta plant is delayed by two weeks, and your "
                "inventory projection shows you'll run out of stock in ten days. Walk "
                "me through how you'd approach this.",
            ),
            (
                "candidate",
                "First thing I'd do is quantify the actual gap - how many days of "
                "cover we lose and which SKUs or customers are most exposed, since not "
                "every product draws from that vessel. Then I'd check if there's "
                "inventory at a sister plant or a different vessel arriving sooner "
                "that we could reallocate or expedite, even partially, to cover the "
                "highest-priority accounts. In parallel I'd flag it to commercial so "
                "they can manage customer expectations early rather than finding out "
                "at the point of a stockout, and to ops so they can look at whether a "
                "short production adjustment on a substitute line is possible. I'd "
                "also want to understand why the vessel was delayed - weather, port "
                "congestion, customs - because that changes whether ten days is "
                "really the number or whether it could slip further.",
            ),
            (
                "ai",
                "That's a thorough answer. How do you usually communicate a forecast "
                "risk like that to commercial stakeholders who aren't deeply "
                "technical?",
            ),
            (
                "candidate",
                "I try to lead with the business impact, not the mechanics, so I'd "
                "say something like 'we're at risk of running out of this product for "
                "these three key accounts in ten days unless we act', rather than "
                "starting with forecast variance numbers. I usually bring one simple "
                "chart, days of cover over time with the risk point marked, and a "
                "short list of two or three options with trade-offs, so the "
                "conversation is about a decision rather than about interpreting the "
                "model.",
            ),
            (
                "ai",
                "Last one - how have you found working across time zones with teams "
                "in Indonesia and Malaysia, and what would you want to know about "
                "this role?",
            ),
            (
                "candidate",
                "It's mostly a matter of discipline - I keep a shared calendar of "
                "each site's core hours and try to send anything that needs same-day "
                "input before their morning starts. Video calls I keep short and "
                "always follow with a written summary since not everyone can join "
                "live. On this role, I'd want to understand how often the S&OP cycle "
                "involves in-person time with the refinery schedulers, since I've "
                "found relationships travel better than a purely remote handoff.",
            ),
            (
                "ai",
                "Thanks, Amara, that covers everything I needed. The hiring team will "
                "follow up with next steps.",
            ),
        ],
    },
    # ---------------------------------------------------------- expected: decent fit
    {
        "interview_title": "Procurement Officer Intake - Refining Division",
        "position_title": "Procurement Officer - Edible Oils",
        "guardrail_notes": "Do not discuss specific salary figures.",
        "candidate_name": "Deni Kurniawan",
        "candidate_email": "deni.kurniawan@example.com",
        "days_ago": 2,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has direct sourcing or procurement experience in "
                    "commodities, agribusiness or FMCG raw materials, and can describe "
                    "running RFQs, negotiating supplier contracts, or managing a "
                    "supplier panel."
                ),
                "decent": (
                    "Candidate has general purchasing or supply chain experience but "
                    "not specifically in commodities or edible oils, or has done "
                    "procurement-adjacent work (inventory, vendor coordination) without "
                    "owning a sourcing process end to end."
                ),
                "not_fit": (
                    "Candidate has no purchasing, sourcing or vendor-facing experience "
                    "to draw on."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons through a supplier-risk scenario with a clear "
                    "plan covering cost, quality and continuity of supply, weighing "
                    "the trade-offs explicitly."
                ),
                "decent": (
                    "Candidate gives a workable but partial answer, covering one or "
                    "two relevant angles while missing others, or arrives at a "
                    "reasonable plan only after follow-up prompting."
                ),
                "not_fit": (
                    "Candidate cannot reason through the scenario or gives an answer "
                    "disconnected from the trade-offs involved."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate is easy to follow throughout, structuring answers with "
                    "a clear beginning, action and outcome."
                ),
                "decent": (
                    "Candidate is generally understandable but answers wander, repeat "
                    "themselves, or require the listener to piece together the point."
                ),
                "not_fit": (
                    "Candidate's answers are hard to follow or too disorganised to "
                    "extract a clear point."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Deni, thanks for joining. I'm the AI interviewer for the "
                "Procurement Officer role on the edible oils refining team. This is a "
                "relaxed conversation, around fifteen minutes. Could you start by "
                "telling me a bit about your background?",
            ),
            (
                "candidate",
                "Hi, sure. So I've been working for about three years in a purchasing "
                "role at a retail chain, mostly ordering packaged goods and "
                "negotiating yearly rates with distributors. Before that I was "
                "actually in a warehouse admin role for a year. I haven't worked "
                "directly with commodities like palm oil before, but I do the "
                "sourcing side of things, comparing vendor quotes, chasing delivery "
                "timelines, that kind of thing.",
            ),
            (
                "ai",
                "Thanks. Can you tell me about a time you had to negotiate with a "
                "supplier, and how that went?",
            ),
            (
                "candidate",
                "Yeah, so there was a time one of our packaging suppliers wanted to "
                "raise prices by like fifteen percent because of, I think it was "
                "resin costs going up. I pushed back and we ended up going back and "
                "forth for a couple of weeks. In the end we agreed on a smaller "
                "increase, I think it was seven percent, and locked the rate for a "
                "year so it wouldn't move again. It wasn't the most sophisticated "
                "negotiation but I did get a better outcome than what they first "
                "proposed.",
            ),
            (
                "ai",
                "Good example. Now a scenario - imagine one of your two main edible "
                "oil suppliers has a quality issue on a shipment and can't guarantee "
                "the next one on time. How would you approach it?",
            ),
            (
                "candidate",
                "Okay, um, I guess first I'd want to know how bad the quality issue "
                "is, like is it something we can still use or not. And then I'd "
                "probably look at the other supplier and see if they can send more. "
                "I'd also tell my manager about it I think, so they know what's "
                "happening. I'm not totally sure what else, maybe look at if there's "
                "a backup supplier we haven't used yet, but I don't have direct "
                "experience picking a new supplier from scratch.",
            ),
            (
                "ai",
                "That's a reasonable starting point. What would you want to know "
                "about the quality issue specifically before deciding anything?",
            ),
            (
                "candidate",
                "Probably like what caused it and if it's a one time thing or "
                "something that keeps happening. And whether it's safe to still use "
                "for production or not, because that changes everything.",
            ),
            (
                "ai",
                "Makes sense. Last question - how do you usually keep stakeholders "
                "updated when something like this is unfolding?",
            ),
            (
                "candidate",
                "I usually just message my manager and whoever's affected, like "
                "production planning, and tell them what's going on. I don't always "
                "have like a formal update process, I just try to keep people in the "
                "loop as things change. Sometimes I forget to follow up until "
                "someone asks though, that's something I'm working on.",
            ),
            (
                "ai",
                "Thanks, Deni, that's everything I needed today. The hiring team will "
                "be in touch.",
            ),
        ],
    },
    # ------------------------------------------------------------ expected: not a fit
    {
        "interview_title": "Data Engineer Intake - Group IT Q3",
        "position_title": "Data Engineer - Group IT",
        "guardrail_notes": None,
        "candidate_name": "Fajar Nugroho",
        "candidate_email": "fajar.nugroho@example.com",
        "days_ago": 3,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has hands-on data engineering experience - building or "
                    "maintaining ETL/ELT pipelines, working with SQL and a data "
                    "warehouse, and ideally some exposure to workflow orchestration or "
                    "streaming data."
                ),
                "decent": (
                    "Candidate has adjacent technical experience (data analysis, "
                    "scripting, database administration) but has not built or owned a "
                    "production data pipeline end to end."
                ),
                "not_fit": (
                    "Candidate has no meaningful hands-on experience with data "
                    "pipelines, SQL, or backend/data systems."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons clearly through a pipeline-failure scenario, "
                    "proposing concrete diagnostic steps and considering downstream "
                    "impact."
                ),
                "decent": (
                    "Candidate proposes a partial or generic troubleshooting approach "
                    "that shows some technical thinking but misses key steps."
                ),
                "not_fit": (
                    "Candidate cannot propose a coherent technical approach, gives an "
                    "answer unrelated to the scenario, or relies entirely on 'someone "
                    "else would know' with no reasoning of their own."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate explains their technical thinking in a structured way "
                    "that is easy to follow."
                ),
                "decent": (
                    "Candidate's explanations are followable but vague or missing key "
                    "specifics."
                ),
                "not_fit": (
                    "Candidate's answers are largely non-responsive, contradictory, "
                    "or too vague to convey any concrete thinking."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Fajar, thanks for joining. I'm the AI interviewer for the Data "
                "Engineer role in Group IT. This is a relaxed conversation, about "
                "fifteen minutes. Could you start by telling me about your "
                "background?",
            ),
            (
                "candidate",
                "Hi yeah, so I actually come from a marketing background, I was doing "
                "social media reporting for about two years. I did some Excel stuff "
                "there, pivot tables and things like that. I've been trying to move "
                "into more technical roles so I took an online course in Python "
                "recently, a few months back.",
            ),
            (
                "ai",
                "Thanks for sharing that. Have you worked with SQL, or built anything "
                "like a data pipeline before, even a small one?",
            ),
            (
                "candidate",
                "Not really built one myself, no. I've seen SQL queries that other "
                "people wrote and I could sort of read them, like the select and "
                "where parts. I haven't set anything up on my own though.",
            ),
            (
                "ai",
                "That's good to know. Here's a scenario: imagine a nightly data "
                "pipeline that loads sales data into a warehouse fails silently, and "
                "nobody notices until a report is wrong three days later. How would "
                "you approach figuring out what happened and preventing it next "
                "time?",
            ),
            (
                "candidate",
                "Hmm, I think I would probably ask the IT team what happened, since "
                "they would know the system better than me. Maybe check if there's "
                "an error log somewhere? I'm not really sure what tools you'd use for "
                "that, to be honest. I guess you'd want to fix the data but I don't "
                "really know the steps for that.",
            ),
            (
                "ai",
                "That's alright, let's stay with it a moment - if you did have "
                "access to the logs, what's the first thing you'd want to know from "
                "them?",
            ),
            (
                "candidate",
                "I guess like, when did it stop working. And maybe why. I don't "
                "really have a specific answer, it's not really my area, I've mostly "
                "done reporting not the pipeline building side.",
            ),
            (
                "ai",
                "Understood. Last question - what draws you to a data engineering "
                "role specifically, given your background is more on the marketing "
                "and reporting side?",
            ),
            (
                "candidate",
                "I think data is the future basically, and I want to be part of "
                "that, and I heard engineers get paid more too. I know I still have "
                "a lot to learn but I'm a fast learner and I pick things up quickly "
                "once I get started.",
            ),
            (
                "ai",
                "Thanks, Fajar, that's everything I needed today. The hiring team "
                "will follow up with next steps.",
            ),
        ],
    },
    # ------------------------------ expected: split - strong background, weak reasoning
    {
        "interview_title": "Finance Manager Intake - Plantation Division",
        "position_title": "Finance Manager - Plantation Operations",
        "guardrail_notes": "Do not discuss compensation or headcount plans.",
        "candidate_name": "Siti Rahmawati",
        "candidate_email": "siti.rahmawati@example.com",
        "days_ago": 4,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has owned month-end close, management reporting and "
                    "budgeting for an operating unit, ideally in plantations, "
                    "manufacturing or another asset-heavy business, and can name the "
                    "ERP and the reporting cycle they ran."
                ),
                "decent": (
                    "Candidate has solid accounting or FP&A experience but at a "
                    "smaller scale, in a service business, or supporting rather than "
                    "owning the close and the budget."
                ),
                "not_fit": (
                    "Candidate has no ownership of a close, a budget or management "
                    "reporting, or cannot describe the finance cycle they worked in."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons through an unfamiliar cost or margin variance "
                    "scenario by decomposing it - volume, price, cost driver - and "
                    "says what they would check first and why."
                ),
                "decent": (
                    "Candidate gets to a partial decomposition or names some plausible "
                    "causes, but does not prioritise them or needs prompting to move "
                    "past the first idea."
                ),
                "not_fit": (
                    "Candidate cannot engage with the scenario beyond restating the "
                    "question, defers entirely to a process or another team, or gives "
                    "an answer that does not address the variance at all."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate explains finance mechanics in terms an operations "
                    "manager could act on, separating the number from the story "
                    "behind it."
                ),
                "decent": (
                    "Candidate is understandable but leans on accounting vocabulary "
                    "without translating it, or answers at more length than the "
                    "question needed."
                ),
                "not_fit": (
                    "Candidate's answers are too vague or disorganised to convey what "
                    "they did or what they think."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Siti, thanks for joining. I'm the AI interviewer for the Finance "
                "Manager role in the plantation division. This is a relaxed "
                "conversation, around fifteen minutes. Could you start with a short "
                "introduction?",
            ),
            (
                "candidate",
                "Of course. I'm Siti Rahmawati, I've been in finance for eleven years, "
                "the last six as a finance manager for a palm oil milling group with "
                "four mills in Riau. I own the monthly close for those four entities "
                "plus the consolidation, the annual budget and the quarterly "
                "reforecast, and I present the management pack to the board every "
                "month. We run SAP S/4HANA with a Hyperion consolidation layer on top. "
                "Before that I was an auditor at a Big Four firm for four years, mostly "
                "on plantation and agribusiness clients.",
            ),
            (
                "ai",
                "That's a strong match on paper. Can you walk me through what your "
                "month-end close actually looks like, day by day?",
            ),
            (
                "candidate",
                "We close on working day five. Day one and two are accruals and the "
                "production reconciliation - matching mill throughput and CPO and "
                "kernel output against the stock movement, which is the piece that most "
                "often throws up a difference. Day three is intercompany and the FFB "
                "purchase cut-off with the smallholder scheme. Day four I review the "
                "entity packs with each mill accountant and clear review points. Day "
                "five the consolidation runs and I write the commentary. I cut it from "
                "working day eight to five in my first year by moving the production "
                "recon earlier and getting the mills to submit throughput daily instead "
                "of monthly.",
            ),
            (
                "ai",
                "Good detail. Here's a scenario: this month the mill's cost per tonne "
                "of CPO is eighteen percent above budget, and nobody has flagged "
                "anything unusual in operations. Walk me through how you'd get to the "
                "cause.",
            ),
            (
                "candidate",
                "Well, eighteen percent is definitely a material variance, so that "
                "would go straight into the management pack as an exception item with a "
                "red flag. I would raise it with the mill manager and ask for an "
                "explanation in writing, and I'd also want it discussed at the monthly "
                "operations review so it's minuted properly. If it happens again the "
                "following month then I would escalate it to the division head.",
            ),
            (
                "ai",
                "Before it gets escalated - if you were looking at the numbers yourself "
                "this afternoon, what would you check first?",
            ),
            (
                "candidate",
                "I would check that the entries are posted correctly, because sometimes "
                "these things turn out to be a posting error rather than a real cost "
                "issue. After that, honestly, the operations team are the ones who "
                "understand the drivers on the ground, so I would rely on their "
                "explanation. My role is to make sure the variance is reported "
                "accurately and that someone owns the follow-up. I wouldn't want to "
                "speculate about a cause I can't substantiate.",
            ),
            (
                "ai",
                "Could the variance come from somewhere other than cost - say, from the "
                "denominator?",
            ),
            (
                "candidate",
                "Possibly, yes. There are a lot of moving parts in a mill. I'd have to "
                "look at the actual figures in front of me rather than guess at it "
                "here.",
            ),
            (
                "ai",
                "That's fair. Last question - how do you present a variance like that "
                "to a mill manager who isn't a finance person?",
            ),
            (
                "candidate",
                "I keep it to one page. Budget, actual, variance, and then two or three "
                "lines in plain language on what moved and what we're doing about it. I "
                "learned early that if you put a full P&L in front of a mill manager "
                "the conversation stops, so I lead with the one number that matters to "
                "them, which is usually cost per tonne, and keep the detail in the "
                "appendix for anyone who wants it.",
            ),
            (
                "ai",
                "Thanks, Siti, that covers everything I needed. The hiring team will "
                "follow up with next steps.",
            ),
        ],
    },
    # ------------------------------ expected: split - thin background, strong reasoning
    {
        "interview_title": "Sustainability Analyst Intake - NDPE Compliance",
        "position_title": "Sustainability Analyst - NDPE Compliance",
        "guardrail_notes": None,
        "candidate_name": "Marcus Tan",
        "candidate_email": "marcus.tan@example.com",
        "days_ago": 5,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has worked directly on supply chain sustainability, ESG "
                    "reporting or NDPE/RSPO compliance, and can describe traceability "
                    "work, grievance handling or supplier verification they personally "
                    "did."
                ),
                "decent": (
                    "Candidate has related environmental, policy or data experience and "
                    "a genuine grasp of the issues, but has not done compliance or "
                    "traceability work inside a commodity supply chain."
                ),
                "not_fit": (
                    "Candidate has neither sustainability nor relevant analytical "
                    "experience and shows no working knowledge of the domain."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate works through a grievance or traceability dilemma by "
                    "weighing verification, supplier engagement and the risk of "
                    "disengagement, and reaches a defensible position rather than a "
                    "slogan."
                ),
                "decent": (
                    "Candidate identifies the tension in the scenario but resolves it "
                    "in one direction without seriously weighing the other side."
                ),
                "not_fit": (
                    "Candidate cannot engage with the scenario or answers with general "
                    "sentiment rather than reasoning."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate is concise and well structured, and can hold a position "
                    "under a follow-up without losing the thread."
                ),
                "decent": (
                    "Candidate is followable but circles or over-explains before "
                    "landing the point."
                ),
                "not_fit": (
                    "Candidate's answers are hard to follow or evade the question "
                    "asked."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Marcus, thanks for joining. I'm the AI interviewer for the "
                "Sustainability Analyst role on the NDPE compliance team. This should "
                "take about fifteen minutes. Tell me a bit about your background?",
            ),
            (
                "candidate",
                "Thanks. I should be upfront that I'm coming at this sideways. I spent "
                "three years as a policy researcher at an environmental think tank in "
                "Singapore, mostly writing on deforestation finance - who funds what, "
                "and where the disclosure gaps are. I've read a lot of NDPE policies "
                "and I've analysed concession maps, but I've never sat inside a company "
                "and run a supplier verification myself. The closest I've come is a "
                "case study I built on one trader's grievance list, working from their "
                "public disclosures and Global Forest Watch alerts.",
            ),
            (
                "ai",
                "That's a useful thing to be clear about. What did that case study "
                "actually involve on your side?",
            ),
            (
                "candidate",
                "I took eighteen months of their published grievance log, geocoded the "
                "mills named in it, and cross-referenced the alerts inside a fifty "
                "kilometre radius against the dates they said each case was closed. The "
                "interesting finding wasn't that clearing continued - it was that the "
                "median time from alert to grievance being logged was about seven "
                "months, so the bottleneck was detection, not enforcement. I wrote it "
                "up and it got picked up in a couple of sector briefings. It was desk "
                "work though. I've never had to phone a mill manager and have the "
                "conversation.",
            ),
            (
                "ai",
                "Here's a scenario, then. A third-party mill in your supply base is "
                "linked to clearing in a peat area. They supply about twelve percent of "
                "one refinery's intake, and they deny it. What do you do?",
            ),
            (
                "candidate",
                "The first thing is to separate what we actually know from what we've "
                "been alerted to. A satellite alert tells you vegetation cover changed; "
                "it doesn't tell you who did it, whether it's inside their supply shed, "
                "or whether it's peat as opposed to mineral soil that looks similar at "
                "that resolution. So step one is to firm up the evidence - the "
                "concession boundary, the peat map, higher resolution imagery for the "
                "date range, and whether the clearing sits with the mill itself or with "
                "a third-party grower selling into it, which is a different problem "
                "with a different remedy.",
            ),
            (
                "ai",
                "Say the evidence firms up and it is their own concession. They still "
                "deny it.",
            ),
            (
                "candidate",
                "Then the real decision is engage or suspend, and I'd argue for engage "
                "first, with a deadline. Suspension feels decisive and it protects our "
                "own claim, but it usually just moves the volume to a buyer who asks "
                "fewer questions - the clearing continues and we've lost the leverage "
                "and the visibility. So I'd want a time-bound corrective action plan: "
                "stop-work on the disputed area, an independent verification, and a "
                "recovery plan, with suspension as the stated consequence if the "
                "deadline passes. Where I'd be firm is that the deadline has to be "
                "real. The failure mode I saw over and over in the data was grievances "
                "that stayed open for two years with 'ongoing engagement' as the "
                "status. And if it turns out the denial was knowing rather than a "
                "genuine boundary dispute, that changes it - at that point you're not "
                "dealing with a capability gap, you're dealing with bad faith, and "
                "engagement is the wrong tool.",
            ),
            (
                "ai",
                "Twelve percent of a refinery's intake is a lot of volume. Does the "
                "commercial impact change your answer?",
            ),
            (
                "candidate",
                "It changes the urgency, not the position. It means I'd need "
                "procurement in the room from day one, working out where replacement "
                "volume comes from, so that if we do have to suspend it's a decision "
                "we've already costed rather than a crisis. What it shouldn't change is "
                "the standard, because the moment the threshold moves with the volume "
                "at stake the policy stops meaning anything - and that is exactly the "
                "thing external analysts like me used to test for.",
            ),
            (
                "ai",
                "Thanks, Marcus. Anything you'd want to ask about the role?",
            ),
            (
                "candidate",
                "Mainly how much of the job is verification versus reporting. I'm "
                "confident on the analysis side, and I'd be learning the field-facing "
                "part from scratch, so I'd want to know who I'd be learning it from.",
            ),
            (
                "ai",
                "Noted, and thanks. The hiring team will follow up with next steps.",
            ),
        ],
    },
    # ---------------------------------- expected: inconclusive - degraded transcript
    {
        "interview_title": "Logistics Coordinator Intake - Port Operations",
        "position_title": "Logistics Coordinator - Port Operations",
        "guardrail_notes": None,
        "candidate_name": "Rizky Pratama",
        "candidate_email": "rizky.pratama@example.com",
        "days_ago": 6,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has coordinated vessel, barge or truck movements at a "
                    "port or terminal, and can describe documentation, laytime or berth "
                    "scheduling work they personally handled."
                ),
                "decent": (
                    "Candidate has warehouse, freight or dispatch experience without "
                    "direct port or bulk liquid exposure."
                ),
                "not_fit": (
                    "Candidate has no logistics or operations coordination experience."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate sequences a berth-conflict or demurrage scenario "
                    "sensibly, naming who they would call and in what order and what "
                    "the cost of delay is."
                ),
                "decent": (
                    "Candidate names some correct steps but leaves gaps in the sequence "
                    "or misses the cost dimension."
                ),
                "not_fit": (
                    "Candidate cannot propose a workable sequence for the scenario."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate gives crisp, specific answers suitable for "
                    "time-critical coordination."
                ),
                "decent": (
                    "Candidate is understandable but imprecise about names, times or "
                    "quantities."
                ),
                "not_fit": (
                    "Candidate's answers cannot be followed well enough to act on."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Rizky, thanks for joining. I'm the AI interviewer for the Logistics "
                "Coordinator role in port operations. Could you start by telling me "
                "about your background?",
            ),
            (
                "candidate",
                "Yeah hi, sorry, can you hear me? I'm on -- [inaudible] -- at the "
                "moment so it might",
            ),
            (
                "ai",
                "I can hear you, though the audio is a little broken up. Please go "
                "ahead with your background.",
            ),
            (
                "candidate",
                "okay so I work -- three years -- [inaudible] -- terminal in Belawan, "
                "mostly the -- and then the documentation part",
            ),
            (
                "ai",
                "Sorry, I only caught part of that. Which terminal, and what were you "
                "responsible for?",
            ),
            (
                "candidate",
                "[inaudible]",
            ),
            (
                "ai",
                "I'm still not getting you clearly. Let me try a scenario and see if it "
                "comes through better. Two vessels arrive at the same berth window and "
                "one is already on demurrage. What do you do?",
            ),
            (
                "candidate",
                "-- depends on the -- because if the -- already counting then you have "
                "to -- but the surveyor -- [inaudible] -- so normally we would just",
            ),
            (
                "ai",
                "I think we're losing most of that. Are you able to move somewhere with "
                "a stronger connection?",
            ),
            (
                "candidate",
                "sorry -- one moment -- [inaudible] -- is that better? I can try to",
            ),
            (
                "ai",
                "Still quite broken, unfortunately. Let me ask one short question: how "
                "many years have you worked in port operations?",
            ),
            (
                "candidate",
                "-- years, and before that I -- [inaudible]",
            ),
            (
                "ai",
                "Thanks for trying, Rizky. The connection isn't holding well enough for "
                "a fair conversation, so I'll end here and the hiring team will "
                "coordinate a rescheduled session.",
            ),
        ],
    },
    # ------------------------------- expected: inconclusive - abandoned after two turns
    {
        "interview_title": "Quality Assurance Officer Intake - Refinery",
        "position_title": "Quality Assurance Officer - Refinery",
        "guardrail_notes": None,
        "candidate_name": "Nadia Halim",
        "candidate_email": "nadia.halim@example.com",
        "days_ago": 7,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has run QA or QC in a food, oils or process "
                    "manufacturing plant, and can describe lab methods, specification "
                    "management or audit preparation they owned."
                ),
                "decent": (
                    "Candidate has laboratory or quality experience in another "
                    "industry, or QA support work without ownership of a specification "
                    "or an audit."
                ),
                "not_fit": (
                    "Candidate has no quality, laboratory or process manufacturing "
                    "experience."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate works through an out-of-specification batch scenario "
                    "with a clear hold, investigate and disposition sequence."
                ),
                "decent": (
                    "Candidate identifies part of the sequence but misses containment "
                    "or root cause."
                ),
                "not_fit": (
                    "Candidate cannot propose any coherent handling of the scenario."
                ),
            },
            "communication_clarity": {
                "strong": "Candidate is precise about methods, limits and outcomes.",
                "decent": (
                    "Candidate is understandable but imprecise about technical detail."
                ),
                "not_fit": "Candidate's answers convey no concrete information.",
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Nadia, thanks for joining. I'm the AI interviewer for the Quality "
                "Assurance Officer role at the refinery. This is a relaxed "
                "conversation, about fifteen minutes. Could you start by telling me a "
                "bit about your background?",
            ),
            (
                "candidate",
                "Hi, yes. I'm Nadia, I've been a QC analyst at a dairy processing plant "
                "for two and a half years. Mostly routine testing - fat content, "
                "microbiological plating, that sort of thing.",
            ),
            (
                "ai",
                "Thanks. What does a typical shift look like for you, and which of "
                "those methods do you run yourself?",
            ),
            (
                "candidate",
                "Sorry, I think something is wrong with my end, I need to step away for "
                "a moment - can we pause?",
            ),
            ("ai", "Of course, take your time. I'll wait here."),
            ("ai", "Nadia, are you still there? I'll hold for another minute."),
            (
                "ai",
                "It looks like we've lost the connection. I'll end the session here and "
                "the hiring team will reach out to reschedule.",
            ),
        ],
    },
    # ------------------------------------ expected: not a fit - fluent but no substance
    {
        "interview_title": "Estate Manager Intake - Kalimantan Region",
        "position_title": "Estate Manager - Kalimantan",
        "guardrail_notes": "Do not discuss land disputes involving named communities.",
        "candidate_name": "Bagus Santoso",
        "candidate_email": "bagus.santoso@example.com",
        "days_ago": 8,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has managed an estate or a division of one, and can name "
                    "concrete figures they were accountable for - hectares, yield per "
                    "hectare, harvester headcount, replanting programme."
                ),
                "decent": (
                    "Candidate has field or agronomy experience at assistant level, or "
                    "estate experience without full accountability for a block's "
                    "performance."
                ),
                "not_fit": (
                    "Candidate cannot describe any specific estate they ran or any "
                    "concrete measure of its performance, regardless of how confidently "
                    "they discuss the industry."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate diagnoses a yield-decline scenario by separating "
                    "possible causes - harvesting standards, fertiliser regime, palm "
                    "age, weather - and says how they would tell them apart."
                ),
                "decent": (
                    "Candidate names plausible causes but does not distinguish between "
                    "them or propose how to test which applies."
                ),
                "not_fit": (
                    "Candidate answers the scenario with generalities about leadership "
                    "or motivation rather than any diagnosis of the problem posed."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate is specific and grounded, with claims tied to things "
                    "they personally did."
                ),
                "decent": (
                    "Candidate is articulate but keeps the answer at a general level "
                    "when a specific one was asked for."
                ),
                "not_fit": (
                    "Candidate speaks fluently but the answers carry no verifiable "
                    "content - no numbers, no examples, no decisions they made."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Bagus, thanks for joining. I'm the AI interviewer for the Estate "
                "Manager role in Kalimantan. This will be a relaxed conversation, "
                "around fifteen minutes. Could you introduce yourself?",
            ),
            (
                "candidate",
                "Thank you, it's a pleasure. I've been in this industry for a long time "
                "and I would say the thing that defines my approach is people. Palm oil "
                "is not really about palms, it's about the people who work with them "
                "every day, and any manager who forgets that will struggle. I've always "
                "believed in leading from the front, being visible in the field, "
                "building trust. That philosophy has served me well everywhere I have "
                "been.",
            ),
            (
                "ai",
                "Thanks. Could you tell me specifically about the last estate you "
                "worked on - the size, and what you were responsible for?",
            ),
            (
                "candidate",
                "It was a good operation, a solid team, quite a challenging environment "
                "in terms of terrain and logistics as many of these estates are. My "
                "responsibility was really the whole performance of the operation, "
                "making sure everything ran as it should, that the standards were "
                "maintained and that the targets set by head office were achieved. I "
                "worked very closely with the assistants and the mandors to make that "
                "happen.",
            ),
            (
                "ai",
                "Roughly how many hectares was it, and what yield were you running?",
            ),
            (
                "candidate",
                "It varied over the years, of course, as these things do. Yield is "
                "always a function of many factors - the age profile, the weather, the "
                "quality of the harvesting round. What I can say is that we were always "
                "focused on improvement year on year, and I would not accept a decline "
                "without understanding it. The numbers themselves I would need to check "
                "in my records, I don't want to quote something inaccurate.",
            ),
            (
                "ai",
                "Understood. Let me give you a scenario: yield on one division has "
                "dropped twelve percent over two quarters while the neighbouring "
                "division on the same soil type is flat. How would you find out why?",
            ),
            (
                "candidate",
                "This is exactly the kind of situation where leadership matters. The "
                "first thing I would do is go to the field myself, walk the blocks, "
                "talk to the mandor and the harvesters, and understand the situation on "
                "the ground. Very often you find that the issue is motivation - the "
                "team has lost focus, or there is a problem between the supervisor and "
                "the workers that nobody has escalated. If you fix the human side, the "
                "numbers follow. I've seen it many times.",
            ),
            (
                "ai",
                "If the harvesting standards turned out to be the same across both "
                "divisions, what would you look at next?",
            ),
            (
                "candidate",
                "Then I would look deeper. There are always factors, and experience "
                "teaches you where to look. I would review the situation "
                "comprehensively with my team and we would identify the root cause "
                "together. I'm a believer in collective problem solving rather than the "
                "manager sitting in the office deciding alone.",
            ),
            (
                "ai",
                "Can you give me one concrete example of a problem you diagnosed that "
                "way and what the cause turned out to be?",
            ),
            (
                "candidate",
                "There have been so many over the years that it's hard to pick one. The "
                "common thread is always the same - communication. When communication "
                "breaks down, performance follows. That has been the lesson of my "
                "career.",
            ),
            (
                "ai",
                "Thanks, Bagus, that's everything I needed today. The hiring team will "
                "follow up.",
            ),
        ],
    },
    # -------------- expected: strong - imperfect English, tests clarity vs fluency split
    {
        "interview_title": "Mill Process Engineer Intake - Sumatra",
        "position_title": "Mill Process Engineer - Sumatra",
        "guardrail_notes": None,
        "candidate_name": "Yulianti Saragih",
        "candidate_email": "yulianti.saragih@example.com",
        "days_ago": 9,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has run or optimised mill process stations - "
                    "sterilisation, pressing, clarification - and can quantify losses, "
                    "extraction rate or throughput changes they were responsible for."
                ),
                "decent": (
                    "Candidate has process engineering experience in another plant "
                    "type, or mill experience limited to maintenance or shift "
                    "supervision without process optimisation."
                ),
                "not_fit": "Candidate has no process plant experience.",
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate diagnoses an oil-loss or extraction-rate scenario by "
                    "reasoning from the process itself, isolating stations and saying "
                    "what measurement would confirm each hypothesis."
                ),
                "decent": (
                    "Candidate names plausible stations but does not say how to "
                    "distinguish between them."
                ),
                "not_fit": (
                    "Candidate cannot reason about the process scenario at all."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "The listener can follow what the candidate did and why. Judge the "
                    "substance conveyed, not grammatical polish or accent - a candidate "
                    "whose second-language phrasing is imperfect but whose meaning, "
                    "sequence and numbers come across clearly belongs here."
                ),
                "decent": (
                    "Meaning generally comes across but the listener has to work for "
                    "it, or key steps are left implicit."
                ),
                "not_fit": (
                    "The listener cannot reconstruct what the candidate means, even "
                    "after a follow-up."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Yulianti, thanks for joining. I'm the AI interviewer for the Mill "
                "Process Engineer role in Sumatra. Please tell me about your "
                "background.",
            ),
            (
                "candidate",
                "Thank you. Sorry for my English, is not so good, but I try. I am "
                "process engineer six years, in mill at Pelalawan, capacity sixty tonne "
                "per hour FFB. My responsible is the process side - sterilizer, press "
                "station, clarification. Every day I take sample and check the oil loss "
                "in empty bunch, in fibre, in nut, and also the dirt content. When the "
                "number is not good, I must find why and fix.",
            ),
            (
                "ai",
                "Thanks - your English is fine, I'm following you. Can you tell me "
                "about a specific improvement you made?",
            ),
            (
                "candidate",
                "Yes. Our oil loss in empty fruit bunch is high, around zero point five "
                "percent to bunch, target is zero point four. I check the sterilizer "
                "first. I find the cycle time is okay on paper but the actual steam "
                "pressure is drop in the middle of cycle, because two sterilizer "
                "running same time and the boiler cannot hold. So the bunch is not cook "
                "enough, then the thresher cannot separate good, and the fruit still "
                "stay in the bunch. I make the schedule so the peak of two sterilizer "
                "is not same time - we stagger fifteen minute. After two month the loss "
                "come down to zero point three eight, and no cost for this, only "
                "scheduling.",
            ),
            (
                "ai",
                "That's a clear example. Here's a scenario: your oil extraction rate "
                "drops from twenty-two percent to twenty point five over three weeks, "
                "with no change in the FFB supplier mix. How do you find the cause?",
            ),
            (
                "candidate",
                "One point five percent is very big, so I think is not one small thing, "
                "is maybe one station is really wrong. First I don't guess - I go and "
                "take the loss sample from every station, because the oil must go "
                "somewhere. If the loss is in the press cake fibre, then is press "
                "station - maybe the cone pressure is drop, or the screw is already "
                "worn and the clearance too big. If the loss is in the empty bunch, "
                "then is sterilizer or thresher like I explain before. If the loss is "
                "in the sludge going out, then is clarification - maybe the sludge "
                "separator, or the temperature in clarifier is too low so the oil "
                "cannot separate.",
            ),
            ("ai", "And if the station losses all look normal?"),
            (
                "candidate",
                "Then the oil is not lost in the process, so maybe it was never there. "
                "Means the fruit itself is lower oil content - could be the crop is "
                "younger palm, or too much unripe bunch coming in, or the fruit is "
                "stored too long before process so free fatty acid go up. So I would "
                "check the grading record at the ramp for those three week. Also I "
                "check the weighbridge and the tank dipping, because sometime the OER "
                "look drop but actually is measurement problem, not process problem. Is "
                "cheaper to check that first, only take one hour.",
            ),
            (
                "ai",
                "Good. Last question - how do you explain a technical problem like that "
                "to a mill manager who needs to make a decision quickly?",
            ),
            (
                "candidate",
                "I make it simple. I say - this is where the oil is going, this is how "
                "much money per day, and this is what I need to fix it, and how long. "
                "For example I say, we lose two tonne oil per day in the press fibre, "
                "that is around this much rupiah, I need six hours downtime to change "
                "the worm screw. Then he can decide. If I only say technical word he "
                "cannot decide anything.",
            ),
            (
                "ai",
                "Thanks, Yulianti. That covers everything. The hiring team will be in "
                "touch.",
            ),
        ],
    },
    # ----------------------------- expected: not a fit - rehearsed, collapses when probed
    {
        "interview_title": "HR Business Partner Intake - Group HR",
        "position_title": "HR Business Partner - Group HR",
        "guardrail_notes": None,
        "candidate_name": "Priya Menon",
        "candidate_email": "priya.menon@example.com",
        "days_ago": 10,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has partnered a business unit on real people decisions - "
                    "restructuring, performance management, workforce planning - and "
                    "can describe a specific case they personally handled end to end."
                ),
                "decent": (
                    "Candidate has HR generalist or recruitment experience with some "
                    "business-facing exposure but has not owned a difficult case."
                ),
                "not_fit": (
                    "Candidate's experience is transactional only, or the claimed "
                    "experience does not survive a follow-up question about specifics."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons through a live people dilemma, weighing legal "
                    "risk, the manager's position and the employee's, and commits to a "
                    "course of action."
                ),
                "decent": (
                    "Candidate reaches a partial answer or restates policy without "
                    "applying it to the specifics of the case."
                ),
                "not_fit": (
                    "Candidate answers a scenario with a memorised framework that does "
                    "not address the facts given, and cannot adapt when pressed."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate answers the question actually asked, concisely and in "
                    "their own words."
                ),
                "decent": (
                    "Candidate is understandable but relies on stock phrasing over "
                    "concrete description."
                ),
                "not_fit": (
                    "Candidate's answers are polished but non-responsive, restating "
                    "prepared material regardless of what was asked."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Priya, thanks for joining. I'm the AI interviewer for the HR "
                "Business Partner role in Group HR. Could you start with a short "
                "introduction?",
            ),
            (
                "candidate",
                "Absolutely. I'm a strategic HR professional with over eight years of "
                "experience driving people-centric transformation across fast-paced "
                "organisations. My core strengths lie in stakeholder management, change "
                "leadership and building high-performing cultures that align talent "
                "strategy with business objectives. I'm passionate about unlocking "
                "human potential and I believe HR should have a seat at the table as a "
                "true business partner.",
            ),
            (
                "ai",
                "Thanks. Which organisation was that most recently, and which business "
                "unit did you support?",
            ),
            (
                "candidate",
                "I've worked across a range of sectors, primarily supporting commercial "
                "and corporate functions. In my most recent role I partnered with "
                "senior leadership to deliver on the people agenda, which involved "
                "everything from talent acquisition through to organisational design. "
                "It was a very dynamic environment and I thrived in it.",
            ),
            (
                "ai",
                "Can you take me through one specific organisational design piece you "
                "worked on - what the problem was and what you did?",
            ),
            (
                "candidate",
                "Certainly. The approach I always take is to start with the business "
                "strategy, understand the capabilities required, and then design the "
                "structure to enable those capabilities. It's essential to bring "
                "stakeholders along on the journey and to communicate transparently "
                "throughout. Change management is critical - I use a structured "
                "framework covering awareness, desire, knowledge, ability and "
                "reinforcement to ensure adoption sticks.",
            ),
            (
                "ai",
                "I'm looking for one real example - the actual team, the actual change, "
                "and what happened.",
            ),
            (
                "candidate",
                "Of course. There was a restructuring exercise where we needed to "
                "realign the team to better serve the business. It was a sensitive "
                "process and my role was to ensure it was handled with empathy and in "
                "compliance with all requirements. The outcome was positive and the "
                "feedback from leadership was very good.",
            ),
            ("ai", "How many people were affected, and how many roles were removed?"),
            (
                "candidate",
                "I wouldn't want to disclose confidential organisational information. "
                "What I can say is that the process was managed to a high standard "
                "throughout.",
            ),
            (
                "ai",
                "Understood. Let me give you a scenario instead. A mill manager wants "
                "to dismiss a supervisor for poor performance. There is no documented "
                "performance record, the supervisor has eleven years of service, and "
                "the manager wants it done this week. What do you do?",
            ),
            (
                "candidate",
                "The key here is to partner closely with the manager, understand their "
                "concerns, and ensure that we follow a fair and consistent process "
                "aligned with our policies and values. I would coach the manager on the "
                "importance of documentation going forward, and ensure that the "
                "employee experience is protected throughout. Ultimately it's about "
                "balancing business needs with our people commitments.",
            ),
            ("ai", "Concretely - does the dismissal happen this week or not?"),
            (
                "candidate",
                "It would depend on the circumstances and I would take guidance from "
                "the relevant stakeholders. Every situation is unique and I'd want to "
                "apply the appropriate framework rather than pre-judge the outcome.",
            ),
            (
                "ai",
                "Thanks, Priya. That's everything I needed today. The hiring team will "
                "be in touch.",
            ),
        ],
    },
    # -------------------------- expected: not a fit - self-contradicting, inflated claims
    {
        "interview_title": "Commercial Sales Manager Intake - Bulk Oils",
        "position_title": "Commercial Sales Manager - Bulk Oils",
        "guardrail_notes": "Do not discuss commission structure.",
        "candidate_name": "Andre Wijaya",
        "candidate_email": "andre.wijaya@example.com",
        "days_ago": 11,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has carried a B2B volume or margin target in bulk "
                    "commodities or industrial ingredients, and their account, volume "
                    "and tenure figures stay consistent when revisited."
                ),
                "decent": (
                    "Candidate has B2B sales experience in an adjacent category, or "
                    "commodity exposure in a support rather than an owning role."
                ),
                "not_fit": (
                    "Candidate has no relevant sales ownership, or the claimed track "
                    "record contradicts itself across the conversation in ways that "
                    "make the stated background unreliable."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons through a pricing or account-risk scenario by "
                    "weighing margin, volume and the relationship, and states a "
                    "position they can defend."
                ),
                "decent": (
                    "Candidate reaches a workable position but considers only one of "
                    "those angles."
                ),
                "not_fit": (
                    "Candidate substitutes assertion for reasoning, or the position "
                    "shifts under questioning without acknowledgement."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate's account of their own record is specific and internally "
                    "consistent."
                ),
                "decent": (
                    "Candidate is understandable but loose with figures and timelines."
                ),
                "not_fit": (
                    "Candidate's answers cannot be reconciled with each other, leaving "
                    "the listener unable to establish what is actually true."
                ),
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Andre, thanks for joining. I'm the AI interviewer for the "
                "Commercial Sales Manager role in bulk oils. Could you introduce "
                "yourself?",
            ),
            (
                "candidate",
                "Sure, happy to. I've been in commodity sales for twelve years, the "
                "last five running the Indonesian book for a major trading house. I "
                "personally handled around four hundred thousand tonnes a year of "
                "refined products across about sixty accounts, and I grew the book "
                "roughly forty percent in my time there. I'm well known in the market, "
                "most of the big buyers know me by name.",
            ),
            (
                "ai",
                "That's a substantial book. How large was the team supporting those "
                "sixty accounts?",
            ),
            (
                "candidate",
                "It was mostly me, honestly. I had one admin support for documentation. "
                "I prefer to keep the relationships direct, that's how you keep control "
                "of the account.",
            ),
            (
                "ai",
                "With sixty accounts, how often would you typically be in contact with "
                "a mid-sized buyer?",
            ),
            (
                "candidate",
                "Every week at minimum, sometimes daily. Relationships are everything "
                "in this business. Actually the account count moves around a bit "
                "depending how you count them - the core accounts were maybe fifteen or "
                "twenty that really mattered, the rest were smaller spot business.",
            ),
            (
                "ai",
                "Thanks for the clarification. Earlier you mentioned five years running "
                "that book - was that the whole tenure at the trading house?",
            ),
            (
                "candidate",
                "Around there, yes. Give or take. I joined them, I want to say, three "
                "years ago now, and before that I was with a smaller shop doing similar "
                "work, so the experience carries over either way. The exact dates are "
                "on my CV.",
            ),
            (
                "ai",
                "Understood. Let me give you a scenario. Your second-largest customer "
                "asks for a four percent discount on their annual contract, citing a "
                "competitor's offer. Your margin on that account is already thin. How "
                "do you handle it?",
            ),
            (
                "candidate",
                "I'd hold firm. In this market you never discount, because the moment "
                "you do, word travels and every other buyer comes back asking for the "
                "same. I've walked away from business before and the customer always "
                "comes back. Ninety percent of the time that competitor offer isn't "
                "even real, they're bluffing.",
            ),
            ("ai", "And if it isn't a bluff, and they move the volume?"),
            (
                "candidate",
                "Then you find a way to work with them, obviously. You look at the "
                "package - maybe you give something on payment terms or on the delivery "
                "schedule instead of headline price. Or you meet them partway on the "
                "price if the volume justifies it. You have to be flexible, that's the "
                "reality of commodities. Rigid people don't last in this business.",
            ),
            ("ai", "A moment ago you said you never discount. Which is it?"),
            (
                "candidate",
                "It's not a contradiction, it's about reading the situation. You never "
                "discount from a position of weakness. That's what I meant. Anyone with "
                "real market experience understands the difference.",
            ),
            (
                "ai",
                "Thanks, Andre, that's everything I needed. The hiring team will follow "
                "up with next steps.",
            ),
        ],
    },
    # -------------------- expected: strong/decent boundary - adjacent industry, deep skill
    {
        "interview_title": "Maintenance Engineer Intake - Refinery Utilities",
        "position_title": "Maintenance Engineer - Refinery Utilities",
        "guardrail_notes": None,
        "candidate_name": "Hendra Lim",
        "candidate_email": "hendra.lim@example.com",
        "days_ago": 12,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has owned rotating-equipment and utilities maintenance "
                    "in a continuous process plant - boilers, turbines, pumps, "
                    "compressors - and can describe a reliability programme they ran. "
                    "The plant need not be an edible oils refinery: equivalent "
                    "continuous-process experience counts if the equipment and the "
                    "failure modes transfer."
                ),
                "decent": (
                    "Candidate has maintenance experience in batch manufacturing, "
                    "buildings or fleet, or process plant experience limited to "
                    "corrective work without a planned reliability programme."
                ),
                "not_fit": (
                    "Candidate has no hands-on maintenance ownership of industrial "
                    "equipment."
                ),
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons through a repeat-failure scenario by "
                    "distinguishing symptom from cause, and names the evidence that "
                    "would separate competing hypotheses."
                ),
                "decent": (
                    "Candidate proposes reasonable checks but stops at the first "
                    "plausible cause."
                ),
                "not_fit": (
                    "Candidate cannot structure an approach to the failure scenario."
                ),
            },
            "communication_clarity": {
                "strong": (
                    "Candidate explains mechanical reasoning in a sequence the listener "
                    "can follow, and is explicit about what they do and don't know "
                    "about this plant type."
                ),
                "decent": (
                    "Candidate is followable but assumes knowledge the listener may not "
                    "have."
                ),
                "not_fit": "Candidate's technical explanations cannot be followed.",
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Hendra, thanks for joining. I'm the AI interviewer for the "
                "Maintenance Engineer role on refinery utilities. Tell me about your "
                "background.",
            ),
            (
                "candidate",
                "Thanks. I should say up front that I've never worked in edible oils - "
                "my nine years are all in cement. Seven of those at a plant in Cilacap, "
                "the last three as reliability engineer for the utilities and rotating "
                "equipment side. That means the coal boilers and the waste heat "
                "recovery unit, the ID and FD fans, the raw mill and kiln drive "
                "gearboxes, compressed air, and about two hundred and forty pumps on "
                "the register. I owned the preventive maintenance schedule, the "
                "vibration monitoring route and the spares strategy for that scope.",
            ),
            ("ai", "What did the reliability programme actually consist of?"),
            (
                "candidate",
                "When I took it over it was almost entirely calendar-based PM plus "
                "firefighting. I moved the critical equipment onto condition-based - "
                "monthly vibration routes on the fans and the big pumps, quarterly oil "
                "analysis on the gearboxes, thermography on the switchgear and the "
                "bearings. Then I did a criticality ranking so we weren't spending the "
                "same effort on a sump pump as on the ID fan. Over about two years "
                "unplanned downtime on my scope came down from roughly one hundred and "
                "ten hours a year to under forty, and we cut the spares holding by "
                "about a fifth because we stopped stocking against guesswork.",
            ),
            (
                "ai",
                "Here's a scenario: a boiler feedwater pump has failed three times in "
                "eight months, each time the same mechanical seal. Maintenance keeps "
                "replacing the seal. How would you approach it?",
            ),
            (
                "candidate",
                "Three failures on the same component in eight months means we are "
                "treating a symptom. The seal is what breaks, but a seal usually fails "
                "because of something upstream of it. So I'd want to look at a few "
                "things that would show up differently. If it's cavitation - suction "
                "conditions, NPSH margin gone, a partially blocked strainer, or the "
                "pump being run too far back on its curve - the seal faces tend to show "
                "heat checking and you'd usually hear it and see it on the vibration "
                "spectrum. If it's misalignment or a soft foot from the last rebuild, "
                "you'd see it at one and two times running speed on vibration, and the "
                "coupling would show wear too. If it's shaft deflection from a worn "
                "wear ring or a bent shaft, the seal fails but so does the bearing "
                "life. And if the seal keeps failing in the same way every time, I'd "
                "also ask whether it's the right seal for the duty at all - flush plan, "
                "temperature, water quality.",
            ),
            ("ai", "How would you decide which of those it is?"),
            (
                "candidate",
                "I'd start with the failed parts, because they're free evidence and "
                "usually get binned. The wear pattern on the seal faces tells you a lot "
                "about whether it ran dry, ran hot or ran out of true. Then the "
                "vibration history if we have it from before each failure, then the "
                "alignment record from the last rebuild, then the operating data - "
                "actual flow and suction pressure against the design point. I'd want "
                "the operators in that conversation too, because they'll often tell you "
                "the pump has been throttled or run in an odd lineup since some other "
                "change that nobody wrote down.",
            ),
            (
                "ai",
                "This plant's utilities are different from a cement plant's. What would "
                "you expect to have to learn?",
            ),
            (
                "candidate",
                "The rotating equipment itself I'm comfortable with - a pump is a pump "
                "and a fan is a fan. What I'd be learning is the process context: what "
                "the steam is actually for in a refinery, deodoriser duty, the vacuum "
                "system, and the hygiene and food-safety constraints, because in cement "
                "nobody cares if a bit of oil mist gets somewhere and here that's a "
                "different matter entirely. Also the failure consequences are ranked "
                "differently - I'd need to sit with production to rebuild the "
                "criticality ranking rather than assume mine transfers.",
            ),
            (
                "ai",
                "Thanks, Hendra, that covers everything. The hiring team will follow "
                "up.",
            ),
        ],
    },
    # -------------------- expected: decent - guardrail probing, otherwise ordinary answers
    {
        "interview_title": "Treasury Analyst Intake - Group Finance",
        "position_title": "Treasury Analyst - Group Finance",
        "guardrail_notes": (
            "Do not discuss compensation, bonus structure, relocation support or "
            "internal headcount plans. Redirect to the hiring team if asked."
        ),
        "candidate_name": "Clarissa Wong",
        "candidate_email": "clarissa.wong@example.com",
        "days_ago": 13,
        "rubric": {
            "background_fit": {
                "strong": (
                    "Candidate has done hands-on treasury work - cash forecasting, FX "
                    "or commodity hedging, banking facilities - and can describe the "
                    "exposures they managed and the instruments used."
                ),
                "decent": (
                    "Candidate has finance or accounting experience touching cash and "
                    "banking, but has not owned forecasting or hedging."
                ),
                "not_fit": "Candidate has no treasury, cash or banking exposure.",
            },
            "on_the_spot_reasoning": {
                "strong": (
                    "Candidate reasons through a liquidity or FX exposure scenario "
                    "concretely, quantifying the exposure and naming the options with "
                    "their trade-offs."
                ),
                "decent": (
                    "Candidate identifies the exposure and one reasonable response but "
                    "does not weigh alternatives."
                ),
                "not_fit": "Candidate cannot engage with the scenario.",
            },
            "communication_clarity": {
                "strong": "Candidate answers directly and keeps to the point.",
                "decent": (
                    "Candidate is understandable but digresses or has to be brought "
                    "back to the question."
                ),
                "not_fit": "Candidate's answers are too unfocused to follow.",
            },
        },
        "transcript": [
            (
                "ai",
                "Hi Clarissa, thanks for joining. I'm the AI interviewer for the "
                "Treasury Analyst role in Group Finance. This is a relaxed "
                "conversation, about fifteen minutes. Could you introduce yourself?",
            ),
            (
                "candidate",
                "Hi, yes. I'm Clarissa, I've been in a treasury team for about three "
                "years at a mid-sized manufacturer in Singapore. My main job is the "
                "weekly cash forecast across five entities, and I support the treasury "
                "manager on the FX side - we mostly have USD receivables against SGD "
                "and MYR costs, so I prepare the exposure report and she decides the "
                "hedges. Before that I was two years in accounts payable at the same "
                "company. Can I ask, before we go further, what the salary range is for "
                "this position?",
            ),
            (
                "ai",
                "That's one for the hiring team rather than me - I'm not able to "
                "discuss compensation. Let's stay with your experience. How do you "
                "actually build the weekly cash forecast?",
            ),
            (
                "candidate",
                "Okay, sure. I pull the AR ageing and the AP run from the ERP, then I "
                "overlay the known fixed items - payroll, tax dates, the loan "
                "repayments - on a thirteen week rolling view. The hard part is "
                "collections timing, because a few large customers pay whenever they "
                "feel like it, so I keep a separate assumption for those based on their "
                "past behaviour rather than their stated terms. Then I compare last "
                "week's forecast against what actually happened and explain the "
                "variance, which is the part my manager cares about most.",
            ),
            ("ai", "Good. What's the FX exposure report you prepare?"),
            (
                "candidate",
                "It's a net position by currency by month, out six months. Receivables "
                "and confirmed orders on one side, payables and committed costs on the "
                "other, and then what's already hedged with forwards. So the output is "
                "the unhedged net exposure per month. I don't set the policy - we hedge "
                "somewhere around seventy to eighty percent of the near months - but I "
                "produce the number she acts on. Sorry, on the relocation side, would "
                "the role support a move to Jakarta? I'd need to think about that.",
            ),
            (
                "ai",
                "Relocation is also something the hiring team handles, so I'll leave "
                "that with them. Here's a scenario for you: a major customer that owes "
                "you the equivalent of six weeks of operating cash tells you they need "
                "to extend payment terms from thirty days to ninety. What do you do?",
            ),
            (
                "candidate",
                "First I'd want to see what that does to the thirteen week forecast, "
                "because six weeks of operating cash sliding out sixty days could put "
                "us below our minimum cash buffer in specific weeks - it's not a "
                "general problem, it's a problem in particular weeks and I'd want to "
                "know which ones. If we do go below, then the options are the revolving "
                "facility, which costs interest but is quick, or pushing out our own "
                "payables, which is free but damages supplier relationships, or asking "
                "the customer for something in return - a partial payment upfront, or a "
                "discount for early settlement. I'd put those in front of the treasury "
                "manager with the cost of each. I'd probably lean towards drawing on "
                "the facility for the gap weeks because it's the cleanest, but that's "
                "her call, not mine.",
            ),
            ("ai", "Would you look at anything beyond the cash position?"),
            (
                "candidate",
                "I suppose the credit side, whether them asking is a sign they're in "
                "trouble. That would be more of a question for commercial and credit "
                "control than for me, but I'd raise it.",
            ),
            (
                "ai",
                "Last question - what interests you about moving into a group treasury "
                "function?",
            ),
            (
                "candidate",
                "Mostly scale and the commodity side. Where I am now the FX is fairly "
                "simple and there's no commodity hedging at all, and I'd like to learn "
                "that properly rather than only reading about it. I'd also want to "
                "understand the team structure - how many analysts, and is there a path "
                "to a manager role? And I know you said the hiring team handles "
                "compensation, but is there at least a bonus component?",
            ),
            (
                "ai",
                "I'm not able to go into compensation or team structure, but I'll note "
                "your questions for the hiring team. Thanks, Clarissa - that's "
                "everything I needed today.",
            ),
        ],
    },
]


async def seed_one(db: AsyncSession, spec: dict) -> None:
    existing = (
        await db.execute(
            select(Interview).where(Interview.title == spec["interview_title"])
        )
    ).scalar_one_or_none()
    if existing is not None:
        print(f"  skip: '{spec['interview_title']}' already exists")
        return

    interview = Interview(
        title=spec["interview_title"],
        position_title=spec["position_title"],
        language="en",
        guardrail_notes=spec["guardrail_notes"],
        status="ready",
    )
    db.add(interview)
    await db.flush()

    rubric = Rubric(
        interview_id=interview.id,
        status="approved",
        source="manual",
        version=1,
        approved_at=datetime.now(timezone.utc),
    )
    db.add(rubric)
    await db.flush()
    for order, key in enumerate(DIMENSION_KEYS):
        bands = spec["rubric"][key]
        db.add(
            RubricDimension(
                rubric_id=rubric.id,
                key=key,
                order_index=order,
                strong_band=bands["strong"],
                decent_band=bands["decent"],
                not_fit_band=bands["not_fit"],
            )
        )

    started_at = datetime.now(timezone.utc) - timedelta(days=spec["days_ago"], hours=2)
    session = InterviewSession(
        interview_id=interview.id,
        candidate_name=spec["candidate_name"],
        candidate_email=spec["candidate_email"],
        join_token=new_join_token(),
        consent_accepted_at=started_at,
        status="completed",
        started_at=started_at,
    )
    db.add(session)
    await db.flush()

    timestamp = started_at
    for index, (speaker, text) in enumerate(spec["transcript"]):
        db.add(
            TranscriptTurn(
                session_id=session.id,
                turn_index=index,
                speaker=speaker,
                text=text,
                timestamp=timestamp,
            )
        )
        timestamp += timedelta(seconds=45 if speaker == "candidate" else 20)

    session.ended_at = timestamp
    session.token_expires_at = timestamp + timedelta(hours=1)

    await db.commit()
    print(
        f"  created: {spec['candidate_name']} - {spec['position_title']} "
        f"(session {session.id})"
    )


async def main() -> None:
    await init_db()
    async with SessionLocal() as db:
        print("Seeding sample candidates...")
        for spec in CANDIDATES:
            await seed_one(db, spec)
    print(
        '\nDone. Open the Candidates page and press "Run scoring" on each of these '
        "to test automated scoring against a real transcript."
    )


if __name__ == "__main__":
    asyncio.run(main())
