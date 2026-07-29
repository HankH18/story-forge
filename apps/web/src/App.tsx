/**
 * Routing — §11: `/` (landing) · `/read/:kidId` (kid mode). The parent home,
 * dashboard and admin portal arrive with auth in phases 6 and 8.
 *
 * `/read` without an id resolves to kid 1 rather than 404ing: until the `kids`
 * table exists there is no id to be wrong about, and a child-facing route that
 * can show a "not found" page contradicts §01.
 */

import { BrowserRouter, Navigate, Route, Routes } from 'react-router-dom'
import KidSession from './routes/KidSession'
import Landing from './routes/Landing'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/read" element={<Navigate to="/read/1" replace />} />
        <Route path="/read/:kidId" element={<KidSession />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  )
}
