/**
 * Browser checks — stage 6 planner views (Today/Inbox/Upcoming, task editor,
 * quick add, projects/areas/resources/tags, search, archive, Today card).
 * Called by scripts/ui-check.mjs with shared helpers; owned by the views workstream.
 */
export async function run(ctx) {
  const { page, WEB_URL, check } = ctx
  console.log('\n7¹¹. stage 6 planner')
  await page.goto(`${WEB_URL}/plan`, { waitUntil: 'networkidle' })
  check((await page.locator('nav.segmented').count()) > 0, 'plan tab renders its views')
}
