# CAMPO P2P

<!-- impeccable:product-schema 1 -->

## Platform

web

## Stack

Delegated: FastAPI and Docker Compose for the simulation backend; a browser interface will visualize and control the topology.

## Users

Students and evaluators of a Software Design course who need to understand a peer-to-peer architecture through a short local demonstration.

## Product Purpose

CAMPO P2P simulates a distributed radiation-detection field. Equivalent peers generate synthetic measurements, discover each other, corroborate candidate events, route confirmed reports and continue operating when nodes or links are disabled.

## Positioning

The product makes P2P behavior observable: discovery, direct peer voting, duplicate prevention, TTL, route selection, failure and resynchronization are visible instead of hidden behind a central server.

## Operating Context

The complete system runs locally with Docker Compose. Four peers begin active and two standby peers can be activated from the interface. The demonstration is designed for approximately six minutes.

## Capabilities and Constraints

- Six prepared peer containers, with four active initially.
- Full-mesh logical topology plus gateway links.
- Synthetic measurements every two seconds and five-minute retention.
- Global simulated neutrino event and an individual false-positive injection.
- Strict N/N and tolerant N-1 confirmation policies.
- Direct HTTP communication, unique packet IDs, TTL and Dijkstra routing.
- Logical peer and link controls; the web application does not receive Docker-engine privileges.
- The gateway receives only events confirmed by the P2P network.
- All scientific readings are explicitly synthetic and are not real radiation measurements.

## Brand Commitments

The product name is CAMPO P2P. The interface must read as a professional deployed experiment while remaining explicit that it is a simulation.

## Evidence on Hand

No external scientific dataset or physical sensor is used. All observations are synthetic demonstration data.

## Product Principles

- The network must keep working without the dashboard or any single peer.
- Every visible network action should correspond to real communication between containers.
- The gateway observes confirmed outcomes but never decides them.
- Failure and recovery must be safe to demonstrate repeatedly.
- Prefer a reliable, explainable implementation over production complexity.

## Accessibility & Inclusion

Status must never rely on color alone; controls require visible labels and keyboard access.
