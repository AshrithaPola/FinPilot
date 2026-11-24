# FinPilot frontend — two-stage build: Vite compile, then static nginx serve.
FROM node:26.13.0-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
# Firebase web config (public: it names the project, it grants nothing).
# Vite bakes VITE_* values from the environment into the bundle.
ARG VITE_FIREBASE_API_KEY
ARG VITE_FIREBASE_AUTH_DOMAIN
ARG VITE_FIREBASE_PROJECT_ID
ARG VITE_FIREBASE_APP_ID
RUN npm run build

FROM nginx:1.27.5-alpine
# The image's entrypoint fills templates/*.template into conf.d at start.
COPY nginx.conf.template /etc/nginx/templates/default.conf.template
COPY --from=build /app/dist /usr/share/nginx/html
ENV PORT=80 BACKEND_URL=http://backend:8000
EXPOSE 80
