FROM node:20-alpine AS build
WORKDIR /app
COPY frontend/package*.json ./
RUN --mount=type=cache,id=mangaflow-npm,target=/root/.npm,sharing=locked npm ci
COPY frontend ./
RUN npm run build

# Dynamic upstream DNS resolution is available in open-source nginx >=1.27.3.
FROM nginx:1.27.5-alpine
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html
EXPOSE 80
