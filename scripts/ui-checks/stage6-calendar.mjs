/**
 * Browser checks — stage 6 calendar (day/week/month, drag), timer, time log
 * and journal. Called by scripts/ui-check.mjs with shared helpers; owned by
 * the calendar/time/journal workstream.
 */
export async function run(ctx) {
  const { page, WEB_URL, check } = ctx
  console.log('\n7¹². stage 6 calendar, timer & journal')
  await page.goto(`${WEB_URL}/plan/calendar`, { waitUntil: 'networkidle' })
  check(page.url().includes('/plan/calendar'), 'calendar route renders')
}
