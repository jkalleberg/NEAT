# importing the required module
import matplotlib.pyplot as plt
 
# x axis values - Values of N
x = [1000, 1000000, 1000000000, 1000000000000, 1000000000000000, 1000000000000000000, 1000000000000000000000, 1000000000000000000000000000000000000000000000000000000000000000000000000000000000000]
# corresponding y axis values - Method1.py "real time"
y = [.165, .161, .160, .167, .167, .168, .161, .178]
 
# plotting the points
plt.plot(x, y)
 
# naming the x axis
plt.xlabel('Value of N')
# naming the y axis
plt.ylabel('Method2.py Elapsed time')
 
# giving a title to my graph
plt.title('Elapsed time vs N')
 
# function to show the plot
plt.show()
