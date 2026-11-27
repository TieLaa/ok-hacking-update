# Changelog

This file records the user-visible changes of each release. The PyAppify launcher
reads it for the update notes shown on the About page; when a version has no entry
here, it falls back to that version's commit messages.

Format: `## <version>` starts a version, `- ` starts one change. The About page
lists the entries of **every** version between the installed one and the target.

## v0.2.10

- Training multiplier records: a new best above 500% is recorded; the monitor page adds an "auto screenshot" switch (off = numbers only)
- Breath-holding difficulty picks a stamina-affordable tier and drops one tier when confirming does not advance
- Shop and gacha rules follow the item classification: training-only success charms, bitter/odd drinks by stamina gap, allowance cards prioritized for all styles, advance payment available from week 5
- Stars on the equipment page are no longer treated as empty slots
- Gacha/minigame styles no longer buy non-essential shop items in the first 6 weeks

## v0.2.9

- Captain and training-style dropdowns are now separate: selecting a captain fills in that captain's style, and the style can be changed on its own at any time (takes effect immediately)
- Unchecking the captain resets the style to "not selected"; a handover run with no captain and no style identifies the captain from the calendar once
- Existing saves get their style filled in from the current captain automatically

## v0.2.8

- Fixed the National Cup build failure: its match handler order is now shared with Quick Match instead of duplicated

## v0.2.7

- Captain filter: the rarity/position checkboxes click reliably now, and the selected captain is recognized right after filtering
- Quick Match: event stages run automatically, retry after a failed stage, and continue when all stars are earned
- National tournament: automatic challenge can be started directly from the monitor page

## v0.2.6

- Training preparation now has three modes: auto select, semi-auto, and preconfigured
- Each run's training result is recorded immediately and summarized when the task stops
- A shop item is no longer discarded when its price is misread as 0

## v0.2.5

- Training subjects without a teammate icon are no longer selectable (including the currently selected one)
- The training score page pauses for 1 second and its final value is used for judging (the register page no longer overrides it)
- Equipment event: an "attribute cap" option is picked first when present; otherwise stamina vs money is chosen by stamina ratio (below half takes stamina, otherwise money)
- Weekend extra-training page: after backing out of the subject page it goes out instead of looping back into training
- Training-camp event: the two bottom cards use the generic event rules (pure stamina recovery is skipped) instead of clicking the narration and getting stuck

## v0.2.1

- Closing the window no longer freezes: OpenVINO telemetry is turned off at startup
- In the training style, the last week's Saturday first visits the arcade for colored cards when there is enough money, and works only if nothing is bought

## v0.2.0

- On a Thursday with low stamina the rest is kept, so Friday's trip is not lost (gacha style)
- Training days no longer rest based on stamina alone: the subject page is opened first, and you back out if it does not fit
- The construction site no longer gets stuck when there is no steel: the "not enough building materials" dialog is recognized and closed
- Saturday extra training with a colored frame is recognized now; when the day has no rest button, the bot trains if stamina allows and goes out otherwise
- "Focus" and "Stamina" are separated, and the focus recovery switch has its real name back
- A random event with two choices no longer stops on the dialog without choosing
- After shopping, 120 pocket money is kept for the two target colored cards at the arcade; colored gacha in the card pool is bought too
- On an outing day the arcade is checked first for the "note inheritance / player position" colored cards
- When "Rowing race!" appears in the park it is chosen first; on the last week's Saturday the park is used only when there is a rowing race, otherwise the bot works
- Choosing the "awareness" subject no longer clicks twice
- Post-training stamina is read from the pill numbers only, taking the smaller of "current / after training", so a stamina increase is not undercounted
- On the fourth week's Wednesday the subject is chosen by post-training stamina, keeping Thursday's session

## v0.1.20

- The "captain" controls in the installed build are no longer all grayed out
- Six-attribute surge cards are not bought before week 7
- When the urgent-hire queue does not get the urgent-hire card, the plan is restored instead of working empty-handed
- Map decisions are recorded per day, so the last outing is not squeezed out by an urgent hire

## v0.1.19

- R-rarity captains now have their style and position recognized when choosing a captain
- The low-focus dialog is handled before the page underneath, so the equipment page no longer loops forever
- Team scores read all 5 positions even when OCR joins the text
- The breath-hold difficulty page is recognized on its own; a card choice skipped by a pause is retried on the next tick
- The rest threshold follows the version and no longer needs to be adjusted in the UI
- With an arcade discount, the gacha style decides by the discounted threshold
- The weekend picnic is decided by the stamina shortfall, and the old setting is dropped
- Page recognition covers 17 more pages; the regression set went from 45.9% to 95.9%

## v0.1.18

- The About page now lists the real changes instead of only the sync tag and version
- The page classification now covers the restaurant, arcade, park, shop and story pages
- Selecting a captain filters by rarity and position first, so you no longer page through all sixteen cards
- The captain filter keeps only the captain's own rarity tier
- The captain filter checkbox is now toggled by clicking its square, not its text label
- Week 7 Saturday goes straight out; Sunday only enters the shop to spend the pocket money
- The weekend work threshold is back to 360
- After the half-price gacha week the weekend work threshold drops to 200
- Same-quality gacha now prefers two recommended attributes instead of being taken by a single jump stat
- The divination result page for Terumi Hinata can now be confirmed properly

## v0.1.17

- The training-day main screen is no longer mistaken for the captain page, so it no longer reports a missing captain
- A new round no longer instantly reports an unreadable captain, and the focus fallback no longer floods the log
- The construction site page can build normally, and event dialogue is no longer taken for a location page
- Event dialogue mentioning the park is no longer taken for the weekend map
- Skipped clicks during a pause no longer count as a click, so waiting time is no longer judged as stuck
- Event point settlement is no longer taken for the player registration page
- The player registration page no longer clicks "Next" for nothing
- A taken-over training session records the captain's name, and an unreadable failure rate no longer trains blindly
- Training no longer falsely reports a failure, and the game can return to the title screen by itself

## v0.1.16

- The bot no longer steals the mouse while you are using the keyboard or mouse

## v0.1.15

- The announcement page at game start can now be closed properly
- Shop purchase order refined: equipment guides and manuals first, attribute drinks after the wanted attributes
- On the third week's weekend the stamina is pulled back above one hundred, and the last day by the sea is no longer a rest day
- The attribute goods in the shop are bought out before the last match
- Release fix: sync the update repository right after pushing the tag

## v0.1.14

- An attribute that is nearly full is no longer trained blindly; the shortfall is filled by position needs
- The restaurant's weekend urgent hire is handled first; when stamina is short the original plan is kept

## v0.1.13

- The weekend no longer follows a fixed schedule: the training style goes to the park, to work or to spend depending on the stamina shortfall
- The training style no longer visits the arcade or the game centre on weekends: it works when stamina allows and saves the money for the shop's milk
- Shop purchases are unified into one priority: cash, milk, stamina, textbooks, equipment, attributes
- The release notes now list this update automatically instead of only showing the install template
- An unregistered item in the shop is kept as a sample and is no longer placed before the registered items
- Roster collection no longer drags nothing or misses rows: reaching the top and bottom is judged by the actual movement
- The shop now recognizes goods by their base name, opens the details to see the effect, and by default does not buy the blue and white ones
- The monitor page can reach the target score without choosing a captain, and continues with the calendar captain after taking over
- The monitor page layout notes are completed and the five missing keys are written back
- When the fishing spot's confirm button does not react, the cheaper option is chosen instead
- The gold frame is no longer missed when a colored frame is shown at the same time
- Training no longer falsely reports a failure when it finishes

## v0.1.11

- The shop now buys "Jackpot" too, which always pays out pocket money
- The installer moved to its own cnb repository, so the next installer no longer grows with it

## v0.1.10

- When both weekend location switches are off, the stamina shortfall decides between the park and the restaurant
- Screenshots now work on Windows 10
- With 360 pocket money the bot goes to the arcade, and works when there is not enough
- The installer is exported to cnb after a release, so domestic downloads do not wait for GitHub

## v0.1.9

- Added a "save debug evidence" switch, off by default in the installed build
- The announcement page at game start no longer blocks the task
- The first run needs the game opened manually once
- Added a "Stop" button to the monitor page: it only stops the training task, not the executor or the queue
- The quick match switch no longer starts the script as a side effect
- The China build no longer connects to GitHub for install and update: it uses the cnb.cool domestic update source
- The application icon was replaced, which also fixed the screenshot tool
