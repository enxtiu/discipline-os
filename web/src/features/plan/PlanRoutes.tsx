/**
 * The Plan tab (stage 6): nested routes under /plan, one lazy chunk.
 *
 *   /plan                today (overdue · today · time blocks · done)
 *   /plan/inbox          captured, not clarified yet
 *   /plan/upcoming       the next days, incl. future repeats
 *   /plan/calendar       day / week / month with drag
 *   /plan/projects[/:id] /plan/areas[/:id] /plan/resources /plan/tags/:id
 *   /plan/search  /plan/archive  /plan/time (tracked time)  /plan/journal (daily review)
 */
import { Navigate, Route, Routes } from 'react-router-dom'

import { PlanLayout } from './PlanLayout'
import { usePlannerQueueRefresh } from './queries'
import { PlanTodayPage } from './views/PlanTodayPage'
import { InboxPage } from './views/InboxPage'
import { UpcomingPage } from './views/UpcomingPage'
import { SearchPage } from './views/SearchPage'
import { ArchivePage } from './views/ArchivePage'
import { TagPage } from './views/TagPage'
import { ProjectsPage } from './para/ProjectsPage'
import { ProjectPage } from './para/ProjectPage'
import { AreasPage } from './para/AreasPage'
import { AreaPage } from './para/AreaPage'
import { ResourcesPage } from './para/ResourcesPage'
import { CalendarPage } from './calendar/CalendarPage'
import { TimePage } from './timer/TimePage'
import { JournalPage } from './journal/JournalPage'

export default function PlanRoutes() {
  usePlannerQueueRefresh()
  return (
    <Routes>
      <Route element={<PlanLayout />}>
        <Route index element={<PlanTodayPage />} />
        <Route path="inbox" element={<InboxPage />} />
        <Route path="upcoming" element={<UpcomingPage />} />
        <Route path="calendar" element={<CalendarPage />} />
        <Route path="projects" element={<ProjectsPage />} />
        <Route path="projects/:id" element={<ProjectPage />} />
        <Route path="areas" element={<AreasPage />} />
        <Route path="areas/:id" element={<AreaPage />} />
        <Route path="resources" element={<ResourcesPage />} />
        <Route path="tags/:id" element={<TagPage />} />
        <Route path="search" element={<SearchPage />} />
        <Route path="archive" element={<ArchivePage />} />
        <Route path="time" element={<TimePage />} />
        <Route path="journal" element={<JournalPage />} />
        <Route path="*" element={<Navigate to="/plan" replace />} />
      </Route>
    </Routes>
  )
}
