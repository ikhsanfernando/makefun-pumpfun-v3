# pump.fun top-5 forecaster

Monte Carlo log-cap diffusion forecaster for make.fun pumpfun-top5-forecast-01.
Reads the 10-minute snapshots, estimates a median drift and a pooled sigma, simulates
paths to each answer read and writes probabilities, size bins and a top5.
Python 3.13, standard library only.
