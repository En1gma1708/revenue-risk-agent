import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { BrowserRouter, Route, Routes } from 'react-router-dom'
import './index.css'
import Landing from './pages/Landing.tsx'
import Dashboard from './pages/Dashboard.tsx'
import CaseDetail from './pages/CaseDetail.tsx'
import TryIt from './pages/TryIt.tsx'
import Architecture from './pages/Architecture.tsx'
import WhatBroke from './pages/WhatBroke.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/dashboard" element={<Dashboard />} />
        <Route path="/case/:caseId" element={<CaseDetail />} />
        <Route path="/try" element={<TryIt />} />
        <Route path="/architecture" element={<Architecture />} />
        <Route path="/what-broke" element={<WhatBroke />} />
      </Routes>
    </BrowserRouter>
  </StrictMode>,
)
