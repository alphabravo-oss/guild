---
sidebar_position: 2
title: "Create an item"
doc_type: how-to
audience: user
---

# Create an item

## Overview

This page shows you how to add an item to your store. <!-- src/app/main.py:15 -->

## Before you start

Set the `DATABASE_URL` variable first, for example `DATABASE_URL=postgres://localhost/app`.

## Steps

1. Open the Items page.
2. Click Add. The `createItem` handler saves it by calling `POST /items/{item_id}`.
3. Check the item appears in the list.

## See also

The items overview.
