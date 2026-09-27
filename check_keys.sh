#!/bin/bash
if [ -z "$GEMINI_API_KEY" ]; then
    echo "Key missing"
else
    echo "Key detected"
fi
