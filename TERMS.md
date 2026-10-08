# TERMS.md

*By using this program/app, you consent to not only knowing the contents, terms, and conditions of this document, but to agreeing to them without legally attacking me. To end your service for this app, please see [article 2](#article2).*

*These terms are subject to change without notice.*

<h3 id = 'intro'>A Brief Overview</h3>

Due to the nature of this app, it constantly scrapes the bot's friend list and saves it to a cloud database. This can mean **the user's privacy rights may appear violated during the usage of this app.** However, I am here to say that, not only is this not the case, but there are also safety measures internally placed within the app to ensure the user is never forced to give out information they do not wish to. If you'd like to immediately see those measures, please [skip to article 2](#article2). To put it simply, since the user's data is constantly being grabbed and stored, I have implemented methods to skip *some* of those steps of the procedure.

#### Scraping Objects
*Field names as referenced by the app; storage types are SQLite3*

| Object | Data Type |
| --- | --- |
| `friendCode` | `text` |
| `network` | `integer` |
| `online` | `boolean` |
| `titleID` | `text` |
| `updateID` | `text` |
| `username` | `text` |
| `message` | `text` |
| `mii` | `text` |
| `joinable` | `boolean` |
| `gameDescription` | `text` |
| `favoriteGame`\* | `bigint` |
| `accountCreation` | `bigint` |
| `lastAccessed` | `bigint` |
| `lastOnline` | `bigint` |
| `lastUpdated` | `bigint` |
| `lastRefresh` | `bigint` |
| `refreshRequested` | `boolean` |

The above objects are scraped and stored by the friend bot.  
\*`favoriteGame` is the user's favorite game  
The `last*` fields are Unix timestamps (`lastAccessed` and `lastRefresh` control when data is due to be refreshed, `lastOnline` is when the console was last seen online, and `lastUpdated` is when the bot last processed it), `accountCreation` is when the console was first registered, `network` is the service the console belongs to (`0` for Nintendo Network, `1` for Pretendo Network), and `refreshRequested` is set when a user asks for their profile to be refreshed. The website shows when the bot last updated a console's data beside that console in your [consoles list](https://3dsrpc.com/consoles).

<h3 id = 'article2'>How Do I Opt-Out?</h3>

The complete way to opt out is to remove your console from your [consoles list](https://3dsrpc.com/consoles) on the website using its **Delete** button. This deletes both the link between your Discord account and the console and all of the data the bot has scraped for it, and the bot stops tracking the console entirely. You should also remove the bot from your 3DS friendlist so it can no longer receive data from your 3DS, but deleting the console on the website is sufficient on its own.

If you would rather keep using the presence features of the app, the following actions each opt you out only partially:

- **Removing the bot from your friendlist** ends its ability to receive your information and will eventually delete the scraped data from the bot's database[\*](#article3), but it also disables its ability to scrape your presence data, which means it can no longer provide you a Discord status. The console stays in your consoles list (marked inactive) until you delete it yourself.
- **Ending your status message with a ` ` (space) character** keeps the presence features working, but clears your stored profile information (username, Mii, status message, and favorite game) and keeps it cleared while the space remains. The bot still records a minimal marker (that it saw the space, and when it last checked) so the website can show a visual flag -- useful if the space was a typo. Presence data (`online`, `titleID`, `updateID`, `joinable`, `gameDescription`), your `friendCode`, and `network` are still stored to power those features. This is picked up automatically on the next [information cycle](#article4); no register is required.

| Status Message | Will Bot Store Profile Data? |
| --- | --- |
| `Hey, all!` | `True` |
|  | `True` |
| ` Hi` | `True` |
| `Hi ` | `False`\* |
| ` ` | `False`\* |

\*A minimal marker (that the message ended in a space, and when it was last checked) is still stored, so the website can show a visual flag -- including when the space was a mistake.

<h3 id = 'article3'>Deleting From Friend List</h3>

When deleting the bot's account from your friend list, it will no longer be able to receive data from your 3DS, including (but not limited to) the aforementioned scraping objects. The bot only treats a console as having removed it after the console has been absent for several consecutive full rotations (see ['user cycling'](#article4)), so it can take considerably longer than ten minutes before the scraped data is deleted from the server. How long that takes depends on how busy the queues are; you can watch them run live on the [status page](https://3dsrpc.com/status). This only removes the scraped profile and presence data; the console itself remains in your own consoles list on the website (marked inactive) until you remove it yourself with the Delete button, which is the complete [opt-out](#article2).

<h3 id = 'article4'>Cycling</h3>

User cycling is broken down into two parts, both of which are updated at different speeds. The below will describe both of them as they are currently in the code (as of the commit writing this).

- Presence cycling
  - The bot alternates between two kinds of passes: a "quick" pass over the consoles that are currently online (plus consoles that have just been registered), and -- every tenth pass -- a "full" pass over every tracked console. Each pass works through its consoles in batches of 100, pausing after each batch for a delay scaled to that batch's size before moving on to the next 100. This is what reloads the display of a user's status. Live progress for both queues is published on the [status page](https://3dsrpc.com/status).
  - A full pass is what checks whether a user has removed the bot from their friendlist. A console must be absent for several consecutive full passes before the bot considers it removed, meaning that it will delete the user's account information after those passes.
- Information cycling
  - Profile data (username, status message, Mii, and favorite game) is re-scraped at most roughly every ten minutes once a console is reached in the queue -- consoles that are offline are only reached on full passes, so for them it can take longer. You can see exactly when each console was last updated in your [consoles list](https://3dsrpc.com/consoles). A console is also always scraped the first time it is processed, which includes whenever:
    * The user registers at the website's register page. *(Does not have to be the first time)*
    * A console client registers the console.
  - The website's Refresh button can request an immediate re-scrape, subject to a one-hour cooldown per console.
  - This is what is required/necessary in order to [opt-out](#article2) of the storage of your account's information. If you choose to opt-out, your profile information is deleted on the next information cycle; no register request is required. Deleting the console on the website, by contrast, takes effect immediately and does not wait for a cycle.
