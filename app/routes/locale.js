const express = require('express');
const path = require('path');
const fs = require('fs');
const router = express.Router();

router.get('/:locale', (req, res) => {
  const locale = req.params.locale;
  const filePath = path.join(__dirname, '..', '..', 'build-src', 'locales', `${locale}.json`);
  if (fs.existsSync(filePath)) {
    res.sendFile(filePath);
  } else {
    res.status(404).json({ error: 'Translation not found' });
  }
});

module.exports = router;
