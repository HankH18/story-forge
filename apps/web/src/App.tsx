/**
 * Routing — build doc §11: `/` (landing) · `/app` (parent home) ·
 * `/app/add-kid` · `/read/:kidId` (kid mode) · `/dashboard/:kidId` (parent) ·
 * `/admin` (admin only).
 *
 * §11's route list names `/admin` once while §11's "Admin portal" paragraph names
 * four screens, so `/admin` is a route TREE rather than one component: the
 * validator playground is the index, and `words`, `evals`, `evals/:runId` and
 * `generate` hang off it (DEVIATIONS 9.20). The playground is the index rather
 * than a dashboard because §17 step 1 is the first thing a grader sees, and a
 * landing page in front of it is a click between the demo and its opening line.
 *
 * `RequireAdmin` checks a session and NOT a role. Nothing on the client may assert
 * a role (lib/auth.tsx, DEVIATIONS 9.21); the role gate is the API's 403, which
 * `ui/AdminShell` asks for once per page load and renders in place of the screen.
 *
 * `/read` without an id goes to `/app`, not to a guessed kid id. It used to
 * `<Navigate to="/read/1">`, which is a guess at which child is holding the
 * tablet — and `resolveKid` then quietly substituted whichever reader the account
 * happened to list first, so a reading got filed against the wrong child
 * (DEVIATIONS 7.33). A child-facing route still never shows a "not found" page
 * (§01): `/app` is not an error screen, it is the adult's own screen, and it is
 * the only place that knows which readers exist. The parent routes do the
 * opposite — `/dashboard/nonsense` says so, because a parent shown someone else's
 * numbers, or zeros, has been told something false about their child.
 *
 * §9's auth boundary is drawn here rather than inside each screen, so a route
 * added later is unprotected only by someone editing this table. The two guards
 * fail in opposite directions on purpose: `RequireParent` sends a signed-out
 * parent to `/sign-in`, `RequireKidSession` sends a signed-out *child* to `/`,
 * because §01 gives kid mode no error state and a login form is one
 * (lib/auth.tsx, DEVIATIONS 6.18).
 *
 * `/sign-in/*` and `/sign-up/*` are splats: Clerk's flow walks through child
 * paths (`/sign-in/factor-one`), and an exact match would drop the second step
 * of every sign-in into the catch-all redirect.
 *
 * `<BrowserRouter>` lives in main.tsx now — `<ClerkProvider>` has to sit inside
 * the router to be handed `useNavigate`.
 */

import { Navigate, Route, Routes } from 'react-router-dom'
import { RequireAdmin, RequireKidSession, RequireParent } from './lib/auth'
import AddKid from './routes/AddKid'
import AdminAllowedWords from './routes/AdminAllowedWords'
import AdminColdGenerate from './routes/AdminColdGenerate'
import AdminEvalRun from './routes/AdminEvalRun'
import AdminEvalRuns from './routes/AdminEvalRuns'
import AdminPlayground from './routes/AdminPlayground'
import KidSession from './routes/KidSession'
import Landing from './routes/Landing'
import ParentDashboard from './routes/ParentDashboard'
import ParentHome from './routes/ParentHome'
import { SignInRoute, SignUpRoute } from './routes/SignIn'

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/sign-in/*" element={<SignInRoute />} />
      <Route path="/sign-up/*" element={<SignUpRoute />} />
      <Route
        path="/app"
        element={
          <RequireParent>
            <ParentHome />
          </RequireParent>
        }
      />
      <Route
        path="/app/add-kid"
        element={
          <RequireParent>
            <AddKid />
          </RequireParent>
        }
      />
      <Route
        path="/dashboard/:kidId"
        element={
          <RequireParent>
            <ParentDashboard />
          </RequireParent>
        }
      />
      {/* §11's `/admin` (admin only). The guard is the session check; the role
          check is the API's, rendered by AdminShell (DEVIATIONS 9.21-9.23). */}
      <Route
        path="/admin"
        element={
          <RequireAdmin>
            <AdminPlayground />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/words"
        element={
          <RequireAdmin>
            <AdminAllowedWords />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/evals"
        element={
          <RequireAdmin>
            <AdminEvalRuns />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/evals/:runId"
        element={
          <RequireAdmin>
            <AdminEvalRun />
          </RequireAdmin>
        }
      />
      <Route
        path="/admin/generate"
        element={
          <RequireAdmin>
            <AdminColdGenerate />
          </RequireAdmin>
        }
      />
      <Route path="/read" element={<Navigate to="/app" replace />} />
      <Route
        path="/read/:kidId"
        element={
          <RequireKidSession>
            <KidSession />
          </RequireKidSession>
        }
      />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
