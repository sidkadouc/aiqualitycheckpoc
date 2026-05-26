/**
 * Ribbon command handlers.
 *
 * These run in a hidden iframe — they should be quick and stateless.
 */

/* global Office */

Office.onReady(() => {
  // Register the command function
  Office.actions.associate("checkDocument", checkDocumentCommand);
});

async function checkDocumentCommand(event: Office.AddinCommands.Event) {
  // This command just opens the task pane — the actual check
  // is triggered from there.  The manifest wires the button
  // to ShowTaskpane, so this is a fallback for ExecuteFunction.
  event.completed();
}
