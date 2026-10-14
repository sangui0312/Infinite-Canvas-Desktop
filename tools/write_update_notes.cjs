'use strict';
// Keep the desktop update announcement readable without rendering remote HTML.
const fs = require('node:fs');
const path = require('node:path');
const yaml = require('js-yaml');
const root = path.resolve(__dirname, '..');
const version = require('../package.json').version;
const notes = JSON.parse(fs.readFileSync(path.join(root, 'backend/static/desktop-update-notes.json'), 'utf8'));
const file = path.join(root, 'dist/latest.yml');
const metadata = yaml.load(fs.readFileSync(file, 'utf8'));
if (metadata.version !== version || notes.version !== version || typeof notes.notes !== 'string' || !notes.notes.trim()) throw new Error('Desktop version and announcement must match.');
metadata.releaseName = `Infinite Canvas Desktop ${version}`;
metadata.releaseNotes = notes.notes;
fs.writeFileSync(file, yaml.dump(metadata, {lineWidth: -1}), 'utf8');
console.log(`Desktop update metadata and announcement verified: ${version}`);
