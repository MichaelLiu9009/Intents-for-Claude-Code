# Control from the outside

*A memo on agent system design, and why this project exists. — Yuchen Liu, August 2026*

A modern agent harness is a capability tool designed to do
everything. That is its value, and that is the governance problem in
one sentence: every mechanism we currently use to control it lives
*inside* the tool's own reasoning realm — system prompts that ask
the model to restrain itself, rules interpreted by the thing being
ruled, approval dialogs answered mid-flow. Controlling an
everything-tool from inside its reasoning will not survive the long
run. An operating system never asks a process to police its own
memory access; it draws an address space around it, from outside.

Consider what the inside-control regime has actually produced: the
permission prompt. Security UX spent two decades measuring what
happens to humans placed in an approval loop — browser-warning
studies found clickthrough rates as high as seventy percent before
the dialogs were redesigned away, and the habituation research
explains why: repetition converts a judgment into a reflex. Agent
harnesses have rebuilt exactly that loop, at higher frequency, over
a wider capability surface. Watch anyone work with an agent for an
hour: approval after approval, each individually reasonable, almost
none individually considered. A control that is always exercised
and never deliberated is not a control. It is a click tax paid to
feel governed.

The failure is not that rules exist. Rules only mean something when
they are scoped, and an everything-tool is the one place scope does
not exist by default. A rule written against an infinite capability
multiplier is the danger itself: allow one bare command surface and
you have allowed everything that surface can reach, forever. The
conclusion I kept arriving at, building this system at my own desk,
is that control must come from outside the reasoning realm — and
that moving it outside forces a change of unit.

Here is the pivot this project is built on. When the unit of control
changes from **actions** to **outcomes** — from "may it run this
command?" to "this is the intent, with its acceptance criteria" —
the unit of governance changes with it: from the individual approval
to **the shape of the boundary**, a capability set aligned with the
agent's declared purpose, granted and revoked atomically with a
single switch. You stop adjudicating keystrokes and start
adjudicating shapes. Mobile operating systems already walked this
road once: install-time blanket grants gave way to per-action
prompts, which drowned in the same fatigue, until the platforms
moved the unit again — scoped permissions, and finally the photo
picker, where the app never receives the capability at all, only
the chosen outcome across the boundary. Every mature permission
system ends up governing shapes, not clicks. Agent harnesses are
simply the newest platform still standing mid-road.

The industry senses this, and its current answers come in three
partial forms. Structured memory that recalls by contextual
relevance. Skills that grow themselves whenever they are triggered.
Permission accretion inferred from a session's usage patterns. Each
is reaching for the same thing, and each will fail the long run in
the same way, because all three grow on a **behavioral** signal —
what happened, what recurred, what got clicked through — where
governance requires an **intentional** one: what was actually ruled.
Memory without a purpose-unit is sediment; it records occurrence,
not endorsement. Skills that learn from unaudited executions let
mistakes compound with a straight face. Boundaries widened along
usage are ratchets trained on the very click-fatigue that made
approvals meaningless. Wherever the unit is behavioral where it
must be intentional, the system fails the same way.

What these three are jointly reaching for is one object: capability,
strategy and boundary, tied together by the user's intention. No
existing harness lets a user draw that object directly. This project
is my experiment in preserving the three as a single unit. Every
asset here is declared once, in conversation, and compiled: its tool
surface (capability), its steps and acceptance criteria (strategy),
its scope and permissions (boundary) land together, are approved
together on one card, and retire together. Nothing grows on its own;
memory supplements a declared strategy and never overrules it.
What accumulates over months of use is a set of such units — and
that growing set, not the model behind it, is what actually defines
what my system can do. It is, in nature, a typed ontology, living
on disk, editable by hand. Its closest relative is the agent
orchestration framework — managed agent teams defined around a
task — with one difference that changes everything: those run in
sandboxes of their own, while this runs on a shared physical
substrate, my desktop. In someone else's sandbox, boundary design
is architectural taste. In your own home, it is a survival
condition.

I make no prediction about models. I make one about interfaces:
agent systems will trend toward governing declared shapes from
outside, because every platform that put humans in an approval loop
has eventually been forced to. This project is what that endpoint
looks like when one person builds it early, uses it daily, and
ships it as evidence.
