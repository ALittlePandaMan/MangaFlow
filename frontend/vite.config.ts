import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import {developmentServer} from './devServer.mjs'

export default defineConfig(({command, mode}) => ({
  plugins: [react(), tailwindcss()],
  ...(command === 'serve' ? {server: developmentServer(mode)} : {}),
}))
