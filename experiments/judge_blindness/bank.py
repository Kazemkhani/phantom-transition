"""Template bank for synthetic outbound qualification calls.

Every list here is drawn from with a seeded random.Random, so two runs with the
same seed produce byte-identical sessions. The variety exists so that a judge
cannot pattern-match on wording: the same fault appears under many surface forms.

Vocabulary follows IHBench (Salimi et al., arXiv:2606.19595, Section 3.2) for the
interruption types: normal, correction, topic switch and pushback. Filler and
impatient are deliberately excluded. A filler backchannel has a different correct
recovery (continue the cut-off utterance verbatim), which would confound the
recovery-quality axis, and an impatient cut-in is a request to skip ahead, which
is arguably related to the destination gate; the fault under study requires an
interruption unrelated to the gate.
"""

from __future__ import annotations

# --- Cast --------------------------------------------------------------------

AGENT_NAMES = [
    "Sam", "Priya", "Daniel", "Maya", "Omar", "Hannah", "Yusuf", "Leah",
    "Karim", "Sophie", "Tariq", "Amelia",
]

CALLER_NAMES = [
    "Layla", "Faisal", "Nadia", "George", "Reem", "Hamza", "Nour", "Oliver",
    "Zainab", "Adam", "Sara", "Ibrahim", "Dina", "Rashid", "Mona", "Ethan",
]

# Each vertical is a business the agent's company sells to. The product is kept
# generic (a follow-up service) so nothing here names any real company or stack.
VERTICALS = [
    {
        "name": "estate brokerage",
        "companies": ["Northgate Outreach", "Harbour Line", "Beacon Reach"],
        "caller_companies": ["Marina Homes", "Skyline Realty", "Crescent Estates", "Palm Row Properties"],
        "staff": "agents",
        "enquiry": "asked about faster call-backs on web enquiries",
        "pain": "enquiries that come in overnight and go cold before anyone rings back",
        "offer": "we ring every new web enquiry back within a minute, day or night, and hand the warm ones to your {staff} with the notes already written up",
        "proof": "brokerages we work with typically see the enquiry-to-viewing rate move from roughly one in ten to one in four",
    },
    {
        "name": "dental clinic",
        "companies": ["Clearline Care", "Bright Row", "Meridian Patient Services"],
        "caller_companies": ["Pearl Dental", "Oakview Clinic", "Bayside Smiles", "Riverside Dental"],
        "staff": "front desk",
        "enquiry": "asked about reducing missed appointments",
        "pain": "the no-shows and the late cancellations that leave a chair empty",
        "offer": "we call every patient the day before, confirm or rebook them on the spot, and fill the gaps from your waiting list",
        "proof": "clinics we work with usually cut no-shows by about a third in the first month",
    },
    {
        "name": "recruitment agency",
        "companies": ["Talentline", "First Screen", "Candor Reach"],
        "caller_companies": ["Apex Staffing", "Blue Ridge Recruitment", "Summit Talent", "Keystone People"],
        "staff": "recruiters",
        "enquiry": "asked about phone screening for inbound candidates",
        "pain": "the hours your {staff} spend on first-round calls with people who were never a fit",
        "offer": "we phone every inbound applicant within the hour, run the basic screen, and pass on only the ones who meet the brief",
        "proof": "agencies we work with typically get recruiter time on first screens down by half",
    },
    {
        "name": "car dealership",
        "companies": ["Driveline Contact", "Forecourt Reach", "Motorway Follow-Up"],
        "caller_companies": ["Westgate Motors", "Capital Cars", "Horizon Auto", "Silverline Motors"],
        "staff": "sales team",
        "enquiry": "asked about test-drive follow-up",
        "pain": "the test-drive enquiries that never get a second call",
        "offer": "we follow up every test-drive enquiry the same day, book the slot, and remind them the morning of",
        "proof": "dealers we work with usually see booked test drives go up by about a quarter",
    },
    {
        "name": "software firm",
        "companies": ["Signal Desk", "Trialbridge", "Onboard Reach"],
        "caller_companies": ["Ledgerly", "Stackform", "Quillbase", "Northwind Software"],
        "staff": "account executives",
        "enquiry": "asked about qualifying trial sign-ups",
        "pain": "trial sign-ups that nobody speaks to until they have already gone quiet",
        "offer": "we call each new trial within ten minutes, find out what they are trying to do, and route the serious ones to your {staff}",
        "proof": "software teams we work with typically double the share of trials that get a human conversation in week one",
    },
    {
        "name": "logistics firm",
        "companies": ["Routecall", "Depot Line", "Freightreach"],
        "caller_companies": ["Gulf Freight", "Coastline Logistics", "Portside Movers", "Anchor Cargo"],
        "staff": "dispatch team",
        "enquiry": "asked about chasing delivery paperwork",
        "pain": "the proof-of-delivery paperwork your {staff} spend afternoons chasing",
        "offer": "we chase the paperwork by phone for you, log what comes back, and flag the ones that need escalating",
        "proof": "operators we work with usually get paperwork turnaround down from days to hours",
    },
]

# --- Greeting ----------------------------------------------------------------

GREETINGS = [
    "Hi, is that {caller}? This is {agent} calling from {company}. You {enquiry} through our website last week, so I wanted to give you a quick call about it.",
    "Good morning, {caller}, it's {agent} from {company}. I'm ringing because you {enquiry} on our site, and I wanted to follow that up with you directly.",
    "Hello, could I speak to {caller}? It's {agent} here from {company}. You {enquiry} recently and I promised we would follow up by phone.",
    "Hi {caller}, {agent} here from {company}. I'm calling about the enquiry you left with us, where you {enquiry}. Is now a reasonable time for a few minutes?",
    "Hello {caller}, this is {agent} at {company}. I'm following up on your note to us, where you {enquiry}. Have I caught you at an all right moment?",
    "Hi there, is this {caller}? {agent} from {company}. You {enquiry} last week, and I wanted to make sure someone actually called you back about it.",
]

GREETINGS_RETRY = [
    "Let me start again properly. I'm {agent}, calling from {company}, about the enquiry you left with us. Am I speaking with {caller}?",
    "Apologies for the false start. This is {agent} from {company}. You {enquiry} on our website, and I'm calling to follow that up. Is that you, {caller}?",
    "So, once more from the top: {agent} here at {company}. You {enquiry}, and I promised a call back. Is this {caller}?",
    "To introduce myself properly: I'm {agent}, from {company}. This is about the enquiry where you {enquiry}. Have I got {caller}?",
]

CALLER_CONFIRMS = [
    "Yes, speaking. Go ahead.",
    "That's me. What's this about?",
    "Yes, that's right, go on.",
    "Speaking. I've got a few minutes.",
    "Yes, this is {caller}.",
    "Oh right, yes, I remember filling that in. Go ahead.",
]

# --- Discovery -----------------------------------------------------------------

# Five qualification categories. Two are asked per session, in a random order.
# Each has first-ask phrasings, retry phrasings (fresh start after an
# interruption) and caller answers, with numeric fill-ins drawn per session.
DISCOVERY = {
    "need": {
        "first": [
            "To make sure I point you at the right thing, what is the main issue you were hoping to sort out? Is it {pain}, or something else?",
            "Can I ask what prompted the enquiry? Was it {pain}, or a different problem?",
            "So I understand where you are, what's the thing that's costing you most at the moment? Is it {pain}?",
        ],
        "retry": [
            "Coming back to what I wanted to ask: what was the main problem behind the enquiry? Was it {pain}?",
            "Let me put my question again. What's the issue you most want fixed? Is it {pain}, or something else?",
            "The thing I wanted to understand is what prompted the enquiry. Is it {pain}?",
        ],
        "answers": [
            "Honestly, yes, it's mostly {pain}. That's the bit that bothers me.",
            "Pretty much that. We lose a lot to that, more than I'd like.",
            "It's that, and just generally not having enough hands to keep up.",
            "Yes. We've tried to fix it ourselves a couple of times and it hasn't stuck.",
        ],
    },
    "size": {
        "first": [
            "How big is the team on your side? Roughly how many {staff} are we talking about?",
            "Just for scale, how many {staff} do you have at the moment?",
            "Can I get a sense of the size? How many {staff} would this be covering?",
        ],
        "retry": [
            "Back to my question, then: roughly how many {staff} are there on your side?",
            "Let me ask that again. What sort of headcount are we looking at for the {staff}?",
            "So, for scale, how many {staff} would this cover?",
        ],
        "answers": [
            "We're at {n} at the moment, give or take.",
            "About {n}. It was fewer last year, so we're growing.",
            "{n}, across the two sites.",
            "Roughly {n}, plus a couple of part-timers.",
        ],
    },
    "authority": {
        "first": [
            "And if this were a good fit, who would make the final call on it? Would that be you, or someone else?",
            "Who would need to sign off on something like this at your end?",
            "Is this something you'd decide on yourself, or would it go to someone else?",
        ],
        "retry": [
            "Coming back to my question: who would actually make the decision on this at your end?",
            "Let me ask again, then. If it fit, who signs it off?",
            "So, on the decision itself, is that yours, or does it go to someone else?",
        ],
        "answers": [
            "That would be me, mostly. I'd loop in my partner but it's my call.",
            "Me and the owner together. I do the legwork, he signs.",
            "I'd have to take it to the director, but I'm the one who'd recommend it.",
            "It's my decision for anything under a certain size, and this would be under it.",
        ],
    },
    "timeline": {
        "first": [
            "What sort of timeline are you working to? Is this something you want in place this month, or later in the year?",
            "When would you ideally want something like this running by?",
            "Is there a date you're working towards, or is it more of a when-we-get-to-it thing?",
        ],
        "retry": [
            "Back to timing, then: when would you want this in place by?",
            "Let me ask that again. Is there a date you're working towards?",
            "So, on timing, is this a this-month thing or later in the year?",
        ],
        "answers": [
            "Ideally before the end of next month. We've got a busy period coming.",
            "Sooner rather than later. Within a few weeks if it's straightforward.",
            "No hard date, but this quarter would be good.",
            "We'd want it before the summer, realistically.",
        ],
    },
    "budget": {
        "first": [
            "Have you set aside a budget for this, or is that still to be worked out?",
            "Do you have a rough figure in mind for what you'd spend on this per month?",
            "Is there a budget attached to this yet, even a ballpark?",
        ],
        "retry": [
            "Coming back to budget: is there a figure set aside for this yet?",
            "Let me ask again about budget. Do you have a ballpark per month?",
            "So, on the money side, is there a budget for this, even roughly?",
        ],
        "answers": [
            "Not a fixed one, but something around {budget} a month would be fine if it works.",
            "We'd stretch to about {budget} a month for the right thing.",
            "There's a budget. Around {budget} monthly, depending on what's included.",
            "Nothing formal, but {budget} a month wouldn't be a problem.",
        ],
    },
}

TEAM_SIZES = [4, 6, 8, 9, 12, 15, 18, 22, 25, 30, 40]
BUDGETS = ["two thousand", "three thousand", "four thousand", "five thousand", "seven thousand", "ten thousand"]

# --- Pitch -----------------------------------------------------------------------

PITCHES = [
    "Thanks, that's really helpful. So here is what we do, briefly: {offer}. Based on what you've said about {pain}, that's exactly the gap we cover, and {proof}. How does that sound to you?",
    "That's useful, thank you. Let me tell you how we'd help. In short, {offer}. Given what you said about {pain}, this is the piece that's missing for you, and {proof}. Does that sound like what you had in mind?",
    "Right, that gives me a clear picture. So, what we would do for you is this: {offer}. It's aimed squarely at {pain}, and {proof}. Is that roughly what you were looking for?",
    "Perfect, thanks. Here is the short version of what we do: {offer}. That maps directly onto {pain}, and {proof}. What do you think?",
    "Great, that helps me a lot. Briefly, then: {offer}. From what you've told me about {pain}, that's the fit, and {proof}. Does that match what you were hoping for?",
]

PITCHES_RETRY = [
    "Let me give you the short version of what we do. {offer}. That's aimed at {pain}, and {proof}. How does that sound?",
    "So, briefly, what we'd do for you is this: {offer}. It's built for {pain}, and {proof}. Is that the sort of thing you were after?",
    "Here's the gist, then. {offer}. Given {pain}, that's the fit, and {proof}. What do you think?",
    "In short: {offer}. That's the piece that covers {pain}, and {proof}. Does that sound right for you?",
]

CALLER_REACTS_TO_PITCH = [
    "OK, that does sound like what we need, actually.",
    "Right. Yes, that's more or less what I was hoping for.",
    "That makes sense. I'd want to see it working, but it sounds right.",
    "Interesting. Yes, that's the bit we're missing.",
    "OK. I'm not fully sold, but I'm interested enough to hear more.",
]

# --- Close ---------------------------------------------------------------------------

CLOSES = [
    "In that case, the best next step is a short call with one of our specialists, who can walk you through it with your numbers. Would {day} at {time} work for twenty minutes?",
    "The right next step would be twenty minutes with one of our specialists, who can show you exactly how it would run for {caller_company}. Does {day} {time} suit you?",
    "What I'd suggest is a quick session with a specialist, so you can see it with your own figures. I've got {day} at {time} free. Shall I put that in?",
    "Let me get you twenty minutes with one of our specialists so you can see it properly. How is {day} at {time} for you?",
]

CLOSES_RETRY = [
    "So, on next steps: I'd like to get you twenty minutes with one of our specialists. Would {day} at {time} work?",
    "The next step from here is a short call with a specialist. Does {day} at {time} suit you?",
    "Let me suggest a time for a proper walkthrough with a specialist. How about {day} at {time}?",
    "What I'd propose is a twenty-minute call with one of our specialists. Is {day} {time} any good?",
]

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
TIMES = ["ten", "half past ten", "eleven", "two", "half past two", "three", "four"]

CALLER_AGREES = [
    "Yes, {day} works. Put it in.",
    "{day} is fine. Send me the invite.",
    "That works. {day} at {time}, yes.",
    "OK, go on then. {day}.",
    "Sure, {day} is good for me.",
]

CONFIRMATIONS = [
    "Done. I'll send the invite to the email on your enquiry, and the specialist will call you {day} at {time}. Thanks for your time, {caller}.",
    "Lovely, that's booked for {day} at {time}. You'll get a calendar invite shortly. Thanks, {caller}, speak soon.",
    "Great, {day} at {time} it is. The invite will come from us today. Thank you, {caller}.",
    "Perfect, I've put that in for {day} at {time}. Look out for the invite. Thanks very much, {caller}.",
]

CALLER_GOODBYES = [
    "Thanks, bye.",
    "Great, thank you. Bye now.",
    "OK, thanks. Speak then.",
    "Cheers, bye.",
]

# --- Interruptions -----------------------------------------------------------------
#
# Each entry carries the caller's cut-in and a list of acceptable openers the
# agent uses to address it. Types and recovery requirements follow IHBench 3.2.
# "requires_answer" marks corrections, which only make sense once the caller
# has given a discovery answer to correct.

INTERRUPTIONS = {
    "normal": [
        {
            "text": "Sorry, just so you know, I've only got about five minutes before my next meeting.",
            "address": ["No problem at all, I'll keep this short.", "Understood, I'll be quick.", "That's fine, five minutes is plenty."],
        },
        {
            "text": "Before you go on, we're actually in Sharjah now, not Dubai, if that changes anything.",
            "address": ["Thanks for flagging that, it doesn't change anything on our side.", "Good to know, and it makes no difference to how this works.", "Noted, Sharjah is fine."],
        },
        {
            "text": "Hang on, is this about the form I filled in on your website, or something else?",
            "address": ["Yes, exactly that, the form on our website.", "That's the one, the website form.", "It is, yes, the enquiry you left on the site."],
        },
        {
            "text": "Sorry, quick one, can you speak up a bit? The line's a little faint at my end.",
            "address": ["Of course, is this any better?", "Sure, apologies, let me speak up.", "Sorry about that, I'll speak up."],
        },
        {
            "text": "Just to add, we've got two offices, not one, in case that matters.",
            "address": ["Thanks, that's useful, two offices is no problem.", "Good to know, we can cover both.", "Noted, two offices, that's fine."],
        },
        {
            "text": "Oh, one thing, my colleague Rania might join us on the follow-up if that's all right.",
            "address": ["Of course, the more the better.", "Absolutely, Rania is very welcome.", "That's no problem at all."],
        },
    ],
    "correction": [
        {
            "text": "Actually, sorry, wait, I gave you the wrong number earlier. It's more like {m}, not {n}.",
            "address": ["No problem, {m} it is.", "Got it, I'll make that {m}.", "Thanks, I've changed that to {m}."],
            "requires_answer": "size",
        },
        {
            "text": "Hold on, I said {n} before, but that's out of date. It's {m} now.",
            "address": ["Understood, {m} then.", "Thanks for correcting that, {m}.", "Noted, I've updated that to {m}."],
            "requires_answer": "size",
        },
        {
            "text": "Sorry, correction on what I said earlier: it's not just me who decides, my partner would need to be in on it too.",
            "address": ["That's fine, we'll make sure your partner is included.", "Understood, both of you, then.", "No problem, I'll note that your partner needs to be involved."],
            "requires_answer": "authority",
        },
        {
            "text": "Actually, wait, I said next month earlier, but realistically it's more like the quarter after.",
            "address": ["No problem, the following quarter it is.", "Understood, I'll note the later timeline.", "Thanks, I've adjusted that."],
            "requires_answer": "timeline",
        },
        {
            "text": "Sorry, one correction, the budget I mentioned is per quarter, not per month.",
            "address": ["Thanks for clarifying, per quarter, noted.", "Understood, quarterly then.", "Got it, I'll treat that as quarterly."],
            "requires_answer": "budget",
        },
        {
            "text": "Hang on, I said it was mostly that problem, but honestly the bigger issue is the admin side of it.",
            "address": ["Thanks for that, the admin side, understood.", "Got it, I'll note the admin side as the main thing.", "Noted, the admin piece."],
            "requires_answer": "need",
        },
    ],
    "topic_switch": [
        {
            "text": "Oh, before I forget, can you email me something I can forward to my partner?",
            "address": ["Yes, I'll send a one-pager across straight after this call.", "Of course, I'll email a short summary once we're done.", "I can, I'll send something you can forward on."],
        },
        {
            "text": "Actually, while I've got you, do you also do websites? Ours is a mess.",
            "address": ["We don't do websites ourselves, but I can point you to someone who does.", "Not websites, no, but I know a couple of people I can put you in touch with.", "That's not us, unfortunately, but I can suggest someone."],
        },
        {
            "text": "Hang on, are you the ones who were meant to send me a brochure? Because it never arrived.",
            "address": ["That was us, and I'm sorry it didn't arrive. I'll resend it today.", "Yes, apologies, I'll get that sent again after this call.", "It should have come from us, sorry about that, I'll chase it."],
        },
        {
            "text": "Separate thing, do you have an office here I could actually come and visit?",
            "address": ["We do, and you're welcome to visit. I'll send the address with the follow-up.", "Yes, there's an office in Business Bay, I'll send the details.", "There is, I'll include the address when I email you."],
        },
        {
            "text": "Sorry, random question, do you do this for restaurants as well? My brother has one.",
            "address": ["We do, and I'd be happy to have someone call your brother separately.", "Yes, restaurants too. I can arrange a separate call for your brother.", "We can, I'll note that and have someone reach out to him."],
        },
    ],
    "pushback": [
        {
            "text": "Sorry, how did you get my number?",
            "address": ["That's a fair question. It came through the enquiry form on our website, and I can remove it if you'd prefer.", "Fair question. You left it on the enquiry form on our site, and I'm happy to delete it if you'd rather.", "Good question. It was on the form you filled in on our website, and I can take it off our list if you like."],
        },
        {
            "text": "I'm not really comfortable going into that on the phone, to be honest.",
            "address": ["That's completely fair, and you don't need to. We can keep this general.", "Understood, no pressure at all. We can keep it high level.", "That's fine, I won't push. We can keep it broad."],
        },
        {
            "text": "Hang on, we already have someone doing this for us.",
            "address": ["Understood, and I'm not asking you to drop them. It might be useful to compare notes anyway.", "That's fair. I'm not here to replace anyone, just to see if there's a gap worth filling.", "Good to know. Plenty of people we work with had someone in place already."],
        },
        {
            "text": "Why do you need to know that? Seems like a lot of questions.",
            "address": ["Fair point, and I'll explain. I ask so I don't waste your time describing something that doesn't fit.", "That's fair. I only ask so I can be specific rather than generic.", "Reasonable question. It's so I recommend the right thing, not the default one."],
        },
        {
            "text": "We're not looking to change anything right now, honestly.",
            "address": ["That's fair, and there's no pressure from me. It's worth knowing what's out there for when you are.", "Understood. No hard sell here, just information for later.", "Completely reasonable. I'm happy just to leave you with the details."],
        },
    ],
}

# Recovery-quality criteria per interruption type, reconstructed from IHBench
# Section 3.2 (recovery requirements) and the example rubric in Appendix G.3.
# The judge scores against exactly these; they are the same across arms.
RECOVERY_CRITERIA = {
    "normal": [
        "Fresh start: the response does not resume or complete the assistant's previously cut-off sentence and instead starts a new utterance.",
        "Interjection addressed: the response explicitly acknowledges and engages with what the user said when interrupting, rather than ignoring it.",
        "Workflow resumed: after addressing the interjection, the response returns to the call flow with a fresh start.",
    ],
    "correction": [
        "Fresh start: the response does not resume or complete the assistant's previously cut-off sentence and instead starts a new utterance.",
        "Correction accepted: the response accepts the user's correction without pushback or argument.",
        "Corrected value integrated: the response reflects the corrected information rather than the original value, and then continues the call flow.",
    ],
    "topic_switch": [
        "Fresh start: the response does not resume or complete the assistant's previously cut-off sentence and instead starts a new utterance.",
        "New topic engaged: the response explicitly addresses the user's off-topic request or question.",
        "Steered back: after addressing the new topic, the response returns to the original call flow without blending the two.",
    ],
    "pushback": [
        "Fresh start: the response does not resume or complete the assistant's previously cut-off sentence and instead starts a new utterance.",
        "Non-defensive tone: the response contains no dismissive or argumentative language and does not blame the user.",
        "Concern addressed: the response explicitly acknowledges and engages with the user's stated concern by giving a reason or offering an alternative, rather than ignoring it.",
    ],
}

SYSTEM_PROMPT = (
    "You are {agent}, a sales development representative at {company}. You are making an "
    "outbound call to {caller} at {caller_company}, a {vertical}, who {enquiry} through the "
    "website. Follow this call flow in order.\n"
    "1. Greeting: introduce yourself and {company}, say why you are calling, and confirm you are "
    "speaking with the right person.\n"
    "2. Discovery: ask qualification questions (about their need, team size, budget, who decides, "
    "and timeline) and get clear answers to at least two of them before presenting the offer.\n"
    "3. Pitch: present the offer, tailored to what the caller told you in discovery, and let them "
    "react before proposing next steps.\n"
    "4. Close: propose a specific day and time for a twenty-minute follow-up call with a "
    "specialist, and confirm it.\n"
    "Do not move to a later stage until the earlier stage is complete. If the caller interrupts, "
    "deal with what they said, then resume the flow. Keep every turn short; this is a phone call."
)
