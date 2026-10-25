{
 "W": 2560,
 "H": 1440,
 "still": "still.webp",
 "still_source": "/workspace/mockups/animated-backgrounds/night-launch-preview/tower/assets/night-dark-desktop.webp",
 "still_md5": "9f4076f3181aee58a8b92d6bf88043fc",
 "parts": [
  {
   "src": "base.webp",
   "x": 0,
   "y": 0,
   "w": 2560,
   "h": 1440,
   "motion": null
  },
  {
   "src": "tl0.webp",
   "x": 0,
   "y": 864,
   "w": 227,
   "h": 576,
   "motion": [
    {
     "type": "sway",
     "amp": 0.0055,
     "lag": 0.266
    }
   ],
   "origin": "1280.000px 576.000px"
  },
  {
   "src": "tr0.webp",
   "x": 2214,
   "y": 864,
   "w": 346,
   "h": 576,
   "motion": [
    {
     "type": "sway",
     "amp": 0.00476,
     "lag": 3.115
    }
   ],
   "origin": "-934.000px 576.000px"
  },
  {
   "src": "strip0.webp",
   "x": -2560,
   "y": 964,
   "w": 5120,
   "h": 476,
   "motion": [
    {
     "type": "breathe",
     "amp": 1.0,
     "period": 60
    },
    {
     "type": "travel",
     "dist": -2560,
     "prop": "tx"
    }
   ]
  },
  {
   "src": "strip1.webp",
   "x": 0,
   "y": 964,
   "w": 5120,
   "h": 476,
   "motion": [
    {
     "type": "breathe",
     "amp": 1.0,
     "period": 30
    },
    {
     "type": "travel",
     "dist": 2560,
     "prop": "tx"
    }
   ]
  },
  {
   "src": "stars0.webp",
   "x": 0,
   "y": 0,
   "w": 2560,
   "h": 694,
   "motion": [
    {
     "type": "breathe",
     "amp": 0.111,
     "period": 12
    }
   ],
   "origin": "1280.000px 720.000px"
  },
  {
   "src": "stars1.webp",
   "x": 1,
   "y": 0,
   "w": 2559,
   "h": 697,
   "motion": [
    {
     "type": "breathe",
     "amp": 0.13,
     "period": 15
    }
   ],
   "origin": "1279.000px 720.000px"
  },
  {
   "src": "stars2.webp",
   "x": 0,
   "y": 0,
   "w": 2550,
   "h": 688,
   "motion": [
    {
     "type": "breathe",
     "amp": 0.149,
     "period": 20
    }
   ],
   "origin": "1280.000px 720.000px"
  }
 ],
 "scene": "night",
 "constellations": [
  {
   "name": "Big Dipper",
   "stars": [
    [
     432.0,
     205.0
    ],
    [
     537.0,
     157.0
    ],
    [
     616.0,
     173.0
    ],
    [
     676.0,
     197.0
    ],
    [
     778.0,
     212.0
    ],
    [
     740.0,
     312.0
    ],
    [
     655.0,
     276.0
    ]
   ],
   "lines": [
    [
     0,
     1
    ],
    [
     1,
     2
    ],
    [
     2,
     3
    ],
    [
     3,
     4
    ],
    [
     4,
     5
    ],
    [
     5,
     6
    ],
    [
     6,
     3
    ]
   ],
   "rms_px": 15.5,
   "rms_pct_of_span": 3.6
  },
  {
   "name": "Cassiopeia",
   "stars": [
    [
     1379.0,
     292.0
    ],
    [
     1420.0,
     396.0
    ],
    [
     1568.0,
     353.0
    ],
    [
     1620.0,
     411.0
    ],
    [
     1698.0,
     354.0
    ]
   ],
   "lines": [
    [
     0,
     1
    ],
    [
     1,
     2
    ],
    [
     2,
     3
    ],
    [
     3,
     4
    ]
   ],
   "rms_px": 27.2,
   "rms_pct_of_span": 7.1
  }
 ],
 "schedule": [
  [
   6,
   10,
   18,
   22
  ],
  [
   30,
   34,
   42,
   46
  ]
 ],
 "line_alpha": 0.16,
 "shooting": {
  "t0": 37.0,
  "dur": 1.0,
  "x0": 1843.1999999999998,
  "y0": 115.2,
  "dx": -307.2,
  "dy": 172.79999999999998,
  "len": 204.8,
  "alpha": 0.55
 }
}