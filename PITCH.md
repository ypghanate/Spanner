# Spanner: the pitch

Spoken length: about 3 minutes 30 seconds without the demo. Numbered to match the slides.

---

## 1. Hook

Hold up the keyboard.

"This keyboard has one dead key. Fixing it costs eight pounds and ten minutes. Most people bin it and spend
sixty. Not because the repair is hard. Because nobody is standing next to them saying: *that one, pull it
straight up, no, the other way.*

Spanner is that person. It watches you through the camera, shows you the exact step as a hologram, talks you
through it, stops you before you make the mistake everyone makes, and finds the part. Hands-free. Let me show
you."

(Demo: 2 minutes. Then the second screen.)

## 2. The problem

"Repair guides are written for people who already know the names of the parts. A video shows someone else's
hands at someone else's speed. You can't ask either of them *am I doing this right?*

So three things go wrong. People don't start, because they don't know if they can. People get stuck halfway,
with the thing in pieces. And people make the same small mistakes everyone makes at that step, because
nothing warned them.

The result is a drawer full of things that were ten minutes from working."

## 3. The solution

"Spanner is a repair coach with eyes.

You put the broken thing in front of the laptop and talk. Spanner knows the procedure, because it built it
from real repair videos and guides. It looks at every frame and works out which step you are on. The 3D model
on screen plays that exact step, on your exact keyboard, with the tool you need to hold. When you finish a
step, it moves on. When you do something wrong, it says so before the damage is done. When you pull out the
broken part, it has already found the replacement, checked that it fits, and told you what you are saving.

And every session makes the next one better. The mistakes you make tonight become the warnings someone else
hears tomorrow."

## 4. The decisions we made

"Four decisions shaped this.

**One real object, done properly.** We picked the Royal Kludge RK61, a hot-swap keyboard, and built a
255-part model of it in code: every keycap, switch, socket, the PCB, the case, the tools. The procedure, the
animations and the camera understanding are all about this keyboard. Depth over breadth.

**Voice only.** No buttons, no tabs, no menus. If you need a hand to tap, you are not repairing. Panels
appear when you ask and disappear when you say close.

**Learn from the real world, never download it.** The procedure comes from three repair videos and two
written guides. The videos were watched, not downloaded. Our own sessions are the training data.

**No mocks.** Every stage you see runs for real: the vision, the voice, the web research, the warehouse. We
can show you the health endpoint. Ten stages, ten real."

## 5. How we used Databricks

"Databricks is where Spanner learns. It sits inside the loop, not next to it.

Every live session writes to Unity Catalog: what the camera saw, which step, how long each decision took,
and every mistake it caught. A Lakeflow pipeline with quality checks turns Claude's labels of the repair
videos into the procedure, and a Vector Search index lets the agent look steps up as it goes.

Then the loop closes. When the same mistake shows up in two or more sessions, Spanner asks Databricks before
every step: *what do people get wrong here?* And it says it out loud. That warning you heard at step seven
came from the two sessions we ran last night.

On the dashboard you can see where people get stuck, the warnings it has learned, the money and e-waste
saved by repairing instead of replacing, and the measured latency of every stage. And Genie sits on the same
tables, so you can just ask it."

## 6. How we used Adaption

"The decision Spanner makes most often is small and fast: *given what the camera sees and the step you are
on, is this step done, and is a known mistake happening?* That is a classification, and a frontier model is
the wrong tool for it: too slow, too expensive, and it needs the internet.

So we fine-tuned that judge with Adaption. The data is real: segments of real repair videos labelled by
Claude, plus frames from our own live sessions, curated in Databricks, cleaned and expanded with Adaptive
Data, trained with AutoScientist.

The fine-tuned model runs on the laptop. No API call for the decision, works with the Wi-Fi off, and Claude
is the automatic fallback if it ever hesitates. The dashboard shows it side by side with Claude Haiku on
held-out real frames: accuracy, mistakes caught, and time per decision.

And it is a flywheel. Every mistake caught tonight lands in Databricks and goes into the next Adaption run.
The more people repair, the better and cheaper the judge gets."

## 7. How we used Linkup

"The moment the old switch comes out, Spanner needs fresh facts from the live web: who sells this part, does
it fit this exact keyboard, is there a recall, and what does a new one cost. That is Linkup.

The agent researches: offers from the official store, AliExpress, eBay, specialist shops. It verifies: each
offer has to be confirmed as compatible on two or more independent sites before we show *fits your RK61*.
It checks for safety recalls in the last two years. And it acts: it makes the repair-or-replace decision,
part cost against a new unit at today's exchange rate, and tells you what you save in money and e-waste.

Every claim links to its page. You can see the pages Linkup read. And it started by itself: Spanner saw the
switch come out and went shopping."

## 8. How we used Tavily

"Tavily has a different job from Linkup. Linkup acts live; Tavily built the knowledge.

Before any session, Tavily searched and extracted the written repair guides for this keyboard. Claude
combined them with the video labels into the eleven-step procedure that Spanner follows and Databricks
stores. Live, Tavily fills the gaps: the product photo for every part on screen, and the current price when a
retailer hides it from crawlers."

## 9. How we used Anthropic

"Claude is the brain, in four places.

It is the eyes: every camera frame goes to Claude with the full procedure in context, and it answers which
step, done or not, mistake or not. It is the voice: when you ask *which way does the switch go in*, Claude
answers with the current step in mind, in two spoken sentences. It is the teacher: Claude watched the repair
videos and read the guides and wrote the procedure, with what *done* looks like and the common mistakes at
every step. And it is the judge of the web: Claude decides whether a Linkup offer really fits this model, and
whether a page actually shows a price.

Prompt caching keeps the procedure in context for every frame; structured outputs keep every answer
machine-readable."

## 10. How we used Figma

"Everything you see on screen was designed in Figma first: the hologram HUD, the step card, the camera panel,
the shopping panel, the warning states, and the dashboard page. Foundations as variables: the cyan on navy,
the monospace readouts, the spacing scale. Components with their states: step in progress, step done,
mistake, learned warning. A clickable prototype of the whole demo flow, which doubled as our storyboard.

From Figma we built the interface directly, and the design file is the backup demo if the live one fails."

## Close

"Spanner turns *I don't know if I can fix this* into *show me.* It watches, it guides, it warns, it shops,
and it learns from everyone who uses it.

Eight pounds, ten minutes, one keyboard out of the bin. Thank you."
