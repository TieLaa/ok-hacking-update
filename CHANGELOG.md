# Changelog

This file records the user-visible changes of each release. The PyAppify launcher
reads it for the update notes shown on the About page; when a version has no entry
here, it falls back to that version's commit messages.

Format: `## <version>` starts a version, `- ` starts one change. The About page
lists the entries of **every** version between the installed one and the target.

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
