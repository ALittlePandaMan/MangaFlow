import React from 'react'
import ReactDOM from 'react-dom/client'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { GlobalDialogProvider } from './components/GlobalDialog'
import './tailwind.css'

ReactDOM.createRoot(document.getElementById('root')!).render(
  <React.StrictMode>
    <GlobalDialogProvider>
      <BrowserRouter>
        <App />
      </BrowserRouter>
    </GlobalDialogProvider>
  </React.StrictMode>,
)
