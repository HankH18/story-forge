/**
 * Routing — build doc §11: `/` (landing) · `/app` (parent home) ·
 * `/app/add-kid` · `/read/:kidId` (kid mode) · `/dashboard/:kidId` (parent).
 * The admin portal arrives in phase 8.
 *
 * `/read` without an id resolves to kid 1 rather than 404ing: a child-facing
 * route that can show a "not found" page contradicts §01. The parent routes do
 * the opposite — `/dashboard/nonsense` says so, because a parent shown someone
 * else's numbers, or zeros, has been told something false about their child.
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
import { RequireKidSession, RequireParent } from './lib/auth'
import AddKid from './routes/AddKid'
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
      <Route path="/read" element={<Navigate to="/read/1" replace />} />
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
